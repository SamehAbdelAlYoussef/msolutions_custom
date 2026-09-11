import json
import logging
import os
from datetime import timedelta

import odoo
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# All paths are relative to the Odoo data_dir (= the filestore mount, shared
# host<->container over the same inodes). The container writes the staged dump
# and the spool request; the host uploader reads them, pushes to B2, writes the
# authoritative record + a result marker, and deletes the staged temp files.
MANUAL_DIR_REL = "manual_backups"                               # staged dumps
SPOOL_DIR_REL = os.path.join(".backup_spool", "requests")       # container -> host
RESULT_DIR_REL = os.path.join(".backup_spool", "results")       # host -> container
LOCK_REL = os.path.join(".backup_locks", "global.lock")         # shared flock


class SaasBackupJob(models.Model):
    _name = "saas.backup.job"
    _description = "Manual (on-demand) backup job"
    _order = "requested_at desc, id desc"

    tenant_id = fields.Many2one("saas.tenant", required=True, ondelete="cascade",
                                index=True)
    tenant_name = fields.Char(required=True, index=True)   # snapshot, survives drop
    state = fields.Selection(
        [("queued", "Queued"), ("running", "Dumping"), ("uploading", "Uploading"),
         ("done", "Done"), ("failed", "Failed")],
        default="queued", required=True, index=True)
    requested_by = fields.Many2one("res.users", readonly=True,
                                   default=lambda self: self.env.uid)
    requested_at = fields.Datetime(readonly=True, default=fields.Datetime.now)
    state_since = fields.Datetime(readonly=True, default=fields.Datetime.now)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    message = fields.Text(readonly=True)
    db_bytes = fields.Float(readonly=True)
    fs_bytes = fields.Float(readonly=True)
    reached_b2 = fields.Boolean(readonly=True)
    restic_snapshot_id = fields.Char(readonly=True)
    # Staging paths while the dump exists; cleared once the host deletes them.
    sql_path = fields.Char(readonly=True)
    fs_path = fields.Char(readonly=True)

    ACTIVE_STATES = ("queued", "running", "uploading")
    _STUCK_DEFAULT_MIN = 30

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_state(self, state, **vals):
        self.write(dict(vals, state=state, state_since=fields.Datetime.now()))

    @api.model
    def _data_dir(self):
        return odoo.tools.config["data_dir"]

    def _cleanup_staged(self):
        """Delete this job's staged dump + its spool request, if any remain."""
        self.ensure_one()
        paths = [self.sql_path, self.fs_path,
                 os.path.join(self._data_dir(), SPOOL_DIR_REL, "%d.json" % self.id)]
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:  # noqa: BLE001
                _logger.exception("SaaS: could not remove staged file %s", p)

    # ------------------------------------------------------------------
    # Cron: module-side lifecycle (dump + finalize). One dump per tick.
    # ------------------------------------------------------------------
    @api.model
    def _cron_tick(self):
        self.env["saas.tenant"]._assert_control_plane()
        self._collect_results()     # finalize jobs the host finished uploading
        self._process_one()         # dump the next queued job (under the lock)

    def _claim_one(self):
        self.env.cr.execute(
            "SELECT id FROM saas_backup_job WHERE state = 'queued' "
            "ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1")
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    def _process_one(self):
        job = self._claim_one()
        if job:
            job._run_dump()

    def _run_dump(self):
        """Take the shared filestore flock (non-blocking), disk pre-flight, then
        reuse saas.tenant._dump_tenant and hand the staged dump to the host. If
        the lock is held (the nightly or another manual dump is running) leave
        the job queued -- one dump globally at a time."""
        self.ensure_one()
        import fcntl
        lock_path = os.path.join(self._data_dir(), LOCK_REL)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o660)
        try:
            try:
                os.fchmod(fd, 0o660)   # ensure the host (root) & container can both open
            except OSError:
                pass
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _logger.info("SaaS: backup lock busy; leaving job %s queued", self.id)
                return
            self._dump_under_lock()
        finally:
            os.close(fd)               # closing the fd releases the flock

    def _dump_under_lock(self):
        self.ensure_one()
        tenant = self.tenant_id
        ok, needed, free, msg = tenant._backup_disk_preflight()
        if not ok:
            self._set_state("failed", message=msg, finished_at=fields.Datetime.now())
            tenant._log("backup_failed", msg)
            self.env.cr.commit()
            return
        self._set_state("running", started_at=fields.Datetime.now())
        self.env.cr.commit()           # surface 'running' to the UI immediately
        dest = os.path.join(self._data_dir(), MANUAL_DIR_REL)
        try:
            sql_path, fs_path, sql_size = tenant._dump_tenant(dest)
        except Exception as exc:        # noqa: BLE001 -- _dump_tenant cleaned up its temp
            detail = str(exc)[:500]
            self._set_state("failed", message=detail, finished_at=fields.Datetime.now())
            tenant._log("backup_failed", detail)
            self.env.cr.commit()
            return
        # Hand off to the host uploader. If writing the spool fails, delete the
        # dump we just made so it never lingers, and fail the job cleanly.
        try:
            fs_size = os.path.getsize(fs_path) if fs_path and os.path.exists(fs_path) else 0
            self.write({"sql_path": sql_path, "fs_path": fs_path or False,
                        "db_bytes": sql_size, "fs_bytes": fs_size})
            self._write_spool()
        except Exception as exc:        # noqa: BLE001
            self.write({"sql_path": sql_path, "fs_path": fs_path or False})
            self._cleanup_staged()
            self._set_state("failed", finished_at=fields.Datetime.now(),
                            message=_("Backup handoff failed: %s", str(exc)[:300]))
            tenant._log("backup_failed", "handoff failed")
            self.env.cr.commit()
            return
        self._set_state("uploading")    # host takes over from here
        self.env.cr.commit()

    def _write_spool(self):
        self.ensure_one()
        spool_dir = os.path.join(self._data_dir(), SPOOL_DIR_REL)
        os.makedirs(spool_dir, exist_ok=True)
        # Paths RELATIVE to the data_dir: the container writes /var/lib/odoo/...,
        # the host reads the same files under /opt/odoo/filestore/... -- each side
        # resolves the relative path against its own root.
        data_dir = self._data_dir()
        payload = {
            "job_id": self.id,
            "tenant": self.tenant_name,
            "sql_rel": os.path.relpath(self.sql_path, data_dir),
            "fs_rel": os.path.relpath(self.fs_path, data_dir) if self.fs_path else "",
            "db_bytes": int(self.db_bytes),
            "fs_bytes": int(self.fs_bytes),
        }
        final = os.path.join(spool_dir, "%d.json" % self.id)
        tmp = final + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, final)

    def _collect_results(self):
        """Read the host's result markers and finalize 'uploading' jobs. The
        markers are root-owned (host-authored); we only read them."""
        result_dir = os.path.join(self._data_dir(), RESULT_DIR_REL)
        if not os.path.isdir(result_dir):
            return
        for fn in sorted(os.listdir(result_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(result_dir, fn)) as fh:
                    res = json.load(fh)
            except Exception:  # noqa: BLE001
                continue
            job = self.browse(res.get("job_id")).exists()
            if not job or job.state != "uploading":
                continue     # already finalized or unknown -> host prunes the marker
            ok = res.get("status") == "ok"
            job._set_state(
                "done" if ok else "failed",
                message=res.get("message") or ("Uploaded to B2" if ok else "Upload failed"),
                reached_b2=bool(res.get("reached_b2")),
                restic_snapshot_id=res.get("restic_snapshot_id") or False,
                finished_at=fields.Datetime.now(),
                sql_path=False, fs_path=False,   # host deleted the staged files
            )
            job.tenant_id._log("backup_ok" if ok else "backup_failed", res.get("message"))
            self.env.cr.commit()

    # ------------------------------------------------------------------
    # Cron: watchdog -- rescue jobs whose worker/host died mid-step.
    # ------------------------------------------------------------------
    @api.model
    def _cron_watchdog(self):
        """A job stuck in 'running'/'uploading' past the timeout means the
        module worker or the host uploader died mid-step. Fail it and delete its
        staged dump so it never sits on disk with nothing to clean it up. Same
        pattern as the tenant provisioning watchdog."""
        self.env["saas.tenant"]._assert_control_plane()
        timeout = int(self.env["ir.config_parameter"].sudo().get_param(
            "msolutions_saas.backup_stuck_timeout_minutes", self._STUCK_DEFAULT_MIN))
        deadline = fields.Datetime.now() - timedelta(minutes=timeout)
        stuck = self.search([("state", "in", ("running", "uploading")),
                             ("state_since", "<", deadline)])
        for job in stuck:
            _logger.warning("SaaS: backup watchdog failing job %s (stuck in '%s')",
                            job.id, job.state)
            prev = job.state
            job._cleanup_staged()
            job._set_state("failed", finished_at=fields.Datetime.now(), message=_(
                "Timed out in '%(prev)s' after %(mins)s minutes -- the worker or "
                "host uploader likely died. Staged files were deleted.",
                prev=prev, mins=timeout))
            job.tenant_id._log("backup_failed", "watchdog timeout")
            self.env.cr.commit()
