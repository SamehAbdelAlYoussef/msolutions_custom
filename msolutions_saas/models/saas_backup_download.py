import json
import logging
import os
import secrets
from datetime import timedelta

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Staging + hand-off dirs, relative to the data_dir (shared inode host<->container).
DL_STAGING_REL = "downloads"                                   # <data_dir>/downloads/<token>/
RESTORE_SPOOL_REL = os.path.join(".backup_spool", "restore_requests")   # container -> host
RESTORE_RESULT_REL = os.path.join(".backup_spool", "restore_results")   # host -> container
LOCK_REL = os.path.join(".backup_locks", "global.lock")

INTERNAL_TTL_HOURS = 1
CUSTOMER_TTL_HOURS = 24 * 30


class SaasBackupDownload(models.Model):
    """A single-use, time-limited backup download link -- and the exfiltration
    log. One row per download request: it records who asked, when, for which
    tenant and WHY, because a dump is every record that customer has and pulling
    it off the platform is exfiltration. Rows are never deleted."""
    _name = "saas.backup.download"
    _description = "Backup download link / data-export log"
    _order = "requested_at desc, id desc"

    tenant_id = fields.Many2one("saas.tenant", ondelete="set null", index=True)
    tenant_name = fields.Char(required=True, index=True)       # snapshot; survives drop
    reason = fields.Text(required=True)                        # WHY -- mandatory
    audience = fields.Selection([("internal", "Internal (developer)"),
                                 ("customer", "Customer (terminated tenant)")],
                                default="internal", required=True)
    source = fields.Selection([("existing", "Existing backup"), ("fresh", "Fresh dump")],
                              required=True)
    # For 'existing': which backup (registry ref) and the timestamp of that data,
    # shown to the requester so they never mistake yesterday's state for today's.
    backup_ref = fields.Char()
    source_date = fields.Datetime(string="Data as of")

    requested_by = fields.Many2one("res.users", readonly=True,
                                   default=lambda self: self.env.uid)
    requested_at = fields.Datetime(readonly=True, default=fields.Datetime.now)
    token = fields.Char(required=True, index=True, copy=False, readonly=True)
    expires_at = fields.Datetime(readonly=True)
    ttl_hours = fields.Integer(readonly=True, default=INTERNAL_TTL_HOURS)

    state = fields.Selection(
        [("dormant", "Awaiting first open"),
         ("pending", "Pending"), ("preparing", "Preparing"), ("ready", "Ready"),
         ("streaming", "Downloading"), ("done", "Downloaded"),
         ("failed", "Failed"), ("expired", "Expired")],
        default="pending", required=True, index=True)
    state_since = fields.Datetime(readonly=True, default=fields.Datetime.now)
    used_at = fields.Datetime(readonly=True)                   # single-use stamp
    error = fields.Text(readonly=True)

    staging_rel = fields.Char(readonly=True)                   # <data_dir>-relative
    source_bytes = fields.Float(readonly=True)                 # data size, for the ETA
    byte_count = fields.Float(readonly=True)                   # bytes streamed
    fs_file_count = fields.Integer(readonly=True)              # integrity: files staged
    attachment_count = fields.Integer(readonly=True)           # integrity: ir_attachment

    # dormant is included so a 30-day customer link expires even before first open
    ACTIVE_STATES = ("dormant", "pending", "preparing", "ready", "streaming")
    _STUCK_DEFAULT_MIN = 30
    # Rough throughput for the ETA on the preparing page (restore + assemble).
    _EST_BYTES_PER_SEC = 30 * 1024 * 1024

    # ------------------------------------------------------------------
    @api.model
    def _data_dir(self):
        return odoo.tools.config["data_dir"]

    def _set_state(self, state, **vals):
        self.write(dict(vals, state=state, state_since=fields.Datetime.now()))

    def _url(self):
        """Built on web.base.url -- the control DB's canonical host -- because the
        download record lives in the control DB, not the tenant DB. (dbfilter
        would route a tenant subdomain to the tenant database, where the token
        does not exist.)"""
        self.ensure_one()
        base = (self.env["ir.config_parameter"].sudo()
                .get_param("web.base.url") or "").rstrip("/")
        return "%s/saas/backup/download/%s" % (base, self.token)

    def _staging_abs(self):
        self.ensure_one()
        return os.path.join(self._data_dir(), self.staging_rel or "")

    def _cleanup_staging(self):
        """Remove this link's staged dump + filestore. Never leaves a customer's
        dataset sitting on disk after the link is spent or expired."""
        import shutil
        for rec in self:
            if not rec.staging_rel:
                continue
            path = rec._staging_abs()
            # confine to <data_dir>/downloads/ so a bad value can't wipe elsewhere
            root = os.path.join(rec._data_dir(), DL_STAGING_REL) + os.sep
            if path.startswith(root) and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Creation (developer, from the dashboard)
    # ------------------------------------------------------------------
    @api.model
    def _create_request(self, tenant, reason, source, backup=None,
                        audience="internal"):
        """Log the export and mint a single-use, time-limited link. Called from
        saas.tenant.action_prepare_download after the developer-group gate."""
        if not (reason or "").strip():
            raise UserError(_("A written reason is required to download a backup."))
        ttl = CUSTOMER_TTL_HOURS if audience == "customer" else INTERNAL_TTL_HOURS
        # Customer links are handed over DORMANT: nothing is staged until the
        # customer actually opens the link (regenerate-on-click, no standing copy).
        state = "dormant" if audience == "customer" else "pending"
        vals = {
            "tenant_id": tenant.id, "tenant_name": tenant.name,
            "reason": reason.strip(), "source": source, "audience": audience,
            "token": secrets.token_urlsafe(32),
            "ttl_hours": ttl,
            "expires_at": fields.Datetime.now() + timedelta(hours=ttl),
            "state": state,
        }
        if source == "existing":
            if not backup:
                raise UserError(_("Pick an existing backup to download."))
            vals.update(backup_ref=backup.ref_key, source_date=backup.backup_date,
                        source_bytes=float(backup.size_bytes or 0.0))
        else:
            u = tenant._disk_usage(tenant).get(tenant.id, {}) if tenant else {}
            vals.update(source_date=fields.Datetime.now(),
                        source_bytes=float(u.get("db", 0) + u.get("files", 0)))
        rec = self.sudo().create(vals)
        _logger.info("SaaS: data-export requested by %s for %s (%s) reason=%r",
                     self.env.user.login, tenant.name, source, vals["reason"][:80])
        if state == "pending":
            rec._trigger_prepare()
        return rec

    def _activate(self):
        """First open of a dormant (customer) link -> begin regeneration. Safe to
        call repeatedly: only a dormant link is moved to pending."""
        self.ensure_one()
        if self.state == "dormant" and (
                not self.expires_at or self.expires_at >= fields.Datetime.now()):
            self._set_state("pending")
            self.env.cr.commit()
            self._trigger_prepare()

    def _status_dict(self):
        """What the preparing page polls: honest state + a size-based ETA. The
        customer can close the page and come back -- the link stays valid until
        it is used or expires."""
        self.ensure_one()
        eta = int((self.source_bytes or 0) / self._EST_BYTES_PER_SEC)
        ready = self.state == "ready"
        return {
            "state": self.state,
            "ready": ready,
            "done": self.state in ("done", "streaming"),
            "failed": self.state in ("failed", "expired"),
            "data_as_of": (self.source_date and
                           self.source_date.strftime("%Y-%m-%d %H:%M UTC")) or "",
            "eta_seconds": eta,
            "expires_at": (self.expires_at and
                           self.expires_at.strftime("%Y-%m-%d %H:%M UTC")) or "",
            "error": self.error or "",
        }

    def _trigger_prepare(self):
        cron = self.env.ref("msolutions_saas.cron_prepare_downloads",
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------
    # Cron: prepare pending links, then finalize host restores
    # ------------------------------------------------------------------
    @api.model
    def _cron_prepare(self):
        self.env["saas.tenant"]._assert_control_plane()
        self._expire_stale()             # flip + clean expired links
        self._finalize_streamed()        # clean up staging for completed downloads
        self._collect_restore_results()  # finalize 'existing' prep from host markers
        job = self._claim_pending()
        if job:
            job._run_prepare()

    def _finalize_streamed(self):
        """Clean up staging for downloads that finished streaming. The controller
        drops a .stream_done marker only once the whole zip reached the client;
        Odoo's WSGI does not reliably fire response close-hooks, so the cron does
        this. An aborted stream leaves no marker and is swept at expiry instead."""
        for rec in self.search([("state", "=", "streaming")]):
            marker = os.path.join(rec._staging_abs(), ".stream_done")
            if os.path.exists(marker):
                try:
                    n = int(open(marker).read() or 0)
                except Exception:  # noqa: BLE001
                    n = 0
                rec._cleanup_staging()
                rec._set_state("done", byte_count=n)
                self.env.cr.commit()

    def _claim_pending(self):
        self.env.cr.execute(
            "SELECT id FROM saas_backup_download WHERE state = 'pending' "
            "ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1")
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    def _fail(self, msg):
        self._set_state("failed", error=msg[:500])
        self._cleanup_staging()
        self.env.cr.commit()
        _logger.warning("SaaS: download %s failed: %s", self.id, msg[:200])

    def _run_prepare(self):
        self.ensure_one()
        if self.expires_at and self.expires_at < fields.Datetime.now():
            self._set_state("expired"); self.env.cr.commit(); return
        if self.source == "fresh":
            self._prepare_fresh()
        else:
            self._request_restore()

    # ---- fresh: dump in the container under the shared lock ----
    def _prepare_fresh(self):
        import fcntl
        lock_path = os.path.join(self._data_dir(), LOCK_REL)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o660)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return  # nightly / another dump holds it; stay pending, retry
            self._do_fresh()
        finally:
            os.close(fd)

    def _do_fresh(self):
        import subprocess
        tenant = self.tenant_id
        if not tenant or tenant.state != "active":
            self._fail("tenant is not active; cannot take a fresh dump"); return
        self._set_state("preparing"); self.env.cr.commit()
        staging = os.path.join(self._data_dir(), DL_STAGING_REL, self.token)
        os.makedirs(staging, exist_ok=True)
        self.write({"staging_rel": os.path.join(DL_STAGING_REL, self.token),
                    "source_date": fields.Datetime.now()})
        sql_path = os.path.join(staging, "dump.sql")
        try:
            tenant._pg_dump(sql_path)      # THE shared pg_dump invocation
        except Exception as exc:           # noqa: BLE001
            self._fail("dump failed: %s" % str(exc)[:300]); return
        # Point-in-time filestore via hardlinks (cp -al): no extra disk, and
        # immutable content-addressed attachments stay consistent with the dump.
        fs_src = odoo.tools.config.filestore(tenant.name)
        fs_dst = os.path.join(staging, "filestore")
        try:
            if os.path.isdir(fs_src):
                subprocess.run(["cp", "-al", fs_src, fs_dst],
                               check=True, capture_output=True, timeout=1200)
            else:
                os.makedirs(fs_dst, exist_ok=True)
        except Exception as exc:           # noqa: BLE001
            self._fail("filestore copy failed: %s" % str(exc)[:200]); return
        if self._integrity_ok(sql_path, fs_dst, live=True):
            self._set_state("ready"); self.env.cr.commit()

    # ---- existing: ask the host to restore the snapshot into staging ----
    def _request_restore(self):
        self._set_state("preparing")
        staging_rel = os.path.join(DL_STAGING_REL, self.token)
        self.write({"staging_rel": staging_rel})
        # Include the locator the host needs: source, the backup's own date, and
        # the restic snapshot id (used when the local copy has aged out, or for
        # manual backups whose local file was deleted after upload).
        b = self.env["saas.backup"].sudo().search(
            [("ref_key", "=", self.backup_ref)], limit=1) if self.backup_ref else None
        spool_dir = os.path.join(self._data_dir(), RESTORE_SPOOL_REL)
        os.makedirs(spool_dir, exist_ok=True)
        payload = {
            "id": self.id, "tenant": self.tenant_name,
            "staging_rel": staging_rel,
            "ref_key": self.backup_ref or "",
            "source": b.source if b else "",
            "date": (b.backup_date.strftime("%Y-%m-%d") if (b and b.backup_date) else ""),
            "snapshot_id": (b.restic_snapshot_id or "") if b else "",
        }
        final = os.path.join(spool_dir, "%d.json" % self.id)
        tmp = final + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, final)
        self.env.cr.commit()

    def _collect_restore_results(self):
        result_dir = os.path.join(self._data_dir(), RESTORE_RESULT_REL)
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
            rec = self.browse(res.get("id")).exists()
            if not rec or rec.state != "preparing":
                continue
            if res.get("status") != "ok":
                rec._fail("restore failed: %s" % (res.get("message") or "")[:300])
                continue
            staging = rec._staging_abs()
            sql_path = os.path.join(staging, "dump.sql")
            fs_dir = os.path.join(staging, "filestore")
            if rec._integrity_ok(sql_path, fs_dir, live=False):
                rec._set_state("ready"); self.env.cr.commit()

    # ------------------------------------------------------------------
    # Integrity gate -- never hand over a backup that restores broken
    # ------------------------------------------------------------------
    def _integrity_ok(self, sql_path, fs_dir, live):
        size = os.path.getsize(sql_path) if os.path.exists(sql_path) else 0
        if size < 4096:
            self._fail("dump implausibly small (%d bytes) -- refusing" % size)
            return False
        fs_count = 0
        for _root, _dirs, files in os.walk(fs_dir):
            fs_count += len(files)
        att = self._attachment_file_count(self.tenant_name) if live else None
        self.write({"byte_count": size, "fs_file_count": fs_count,
                    "attachment_count": att or 0})
        # For a FRESH dump the live DB tells us how many distinct files the
        # attachments expect; fewer on disk means a truncated/missing filestore.
        # For an EXISTING backup restic already checksum-verified every file on
        # restore, so a size + presence check is enough.
        if live and att is not None and fs_count < att:
            self._fail("filestore incomplete: %d files staged, ir_attachment "
                       "expects %d -- refusing" % (fs_count, att))
            return False
        return True

    @api.model
    def _attachment_file_count(self, tenant_name):
        """DISTINCT store_fname count from the tenant DB via raw psycopg (Odoo
        dedups filestore files by content, so one file per distinct store_fname).
        Never opens a tenant registry. Returns None if it cannot be read."""
        from contextlib import closing
        try:
            conn = odoo.sql_db.db_connect(tenant_name)
            with closing(conn.cursor()) as cr:
                cr.execute("SELECT COUNT(DISTINCT store_fname) FROM ir_attachment "
                           "WHERE store_fname IS NOT NULL AND store_fname <> ''")
                return int(cr.fetchone()[0])
        except Exception:  # noqa: BLE001
            _logger.exception("SaaS: could not count ir_attachment for %s", tenant_name)
            return None

    # ------------------------------------------------------------------
    # Zip extras
    # ------------------------------------------------------------------
    def _manifest_json(self):
        """Odoo-native manifest.json bytes (version + db_name + installed
        modules). Modules come from the live tenant DB for a fresh dump; for an
        existing backup they may be unavailable, so the field is best-effort."""
        self.ensure_one()
        from contextlib import closing
        modules = {}
        try:
            conn = odoo.sql_db.db_connect(self.tenant_name)
            with closing(conn.cursor()) as cr:
                cr.execute("SELECT name, latest_version FROM ir_module_module "
                           "WHERE state = 'installed'")
                modules = {n: v for n, v in cr.fetchall()}
        except Exception:  # noqa: BLE001
            modules = {}
        m = {
            "odoo_dump": "1",
            "db_name": self.tenant_name,
            "version": odoo.release.version,
            "version_info": odoo.release.version_info,
            "major_version": odoo.release.major_version,
            "modules": modules,
            "exported_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_as_of": (self.source_date and
                           self.source_date.strftime("%Y-%m-%dT%H:%M:%SZ")) or "",
        }
        return json.dumps(m, indent=2).encode()

    def _readme_txt(self):
        self.ensure_one()
        return (
            "RESTORE THIS BACKUP LOCALLY -- commands, run in order\n"
            "=====================================================\n"
            "# 1. database\n"
            "createdb -O $USER %(db)s_local\n"
            "psql -d %(db)s_local -f dump.sql\n"
            "\n"
            "# 2. filestore -> <odoo-data-dir>/filestore/%(db)s_local/\n"
            "#    (copy the CONTENTS of the filestore/ folder in this zip there)\n"
            "mkdir -p ~/.local/share/Odoo/filestore/%(db)s_local\n"
            "cp -a filestore/. ~/.local/share/Odoo/filestore/%(db)s_local/\n"
            "\n"
            "# 3. reset the admin password on the LOCAL copy\n"
            "psql -d %(db)s_local -c \"UPDATE res_users SET password='admin' \"\\\n"
            "  \"WHERE login='admin';\"   # then change it after first login\n"
            "\n"
            "# 4. run it\n"
            "odoo -d %(db)s_local\n"
            "\n"
            "WARNING\n"
            "=======\n"
            "# The dump was made with pg_dump --no-owner: object ownership on the\n"
            "# local copy differs from production. This file is for a LOCAL debug\n"
            "# copy only -- DO NOT restore it straight onto production.\n"
            % {"db": self.tenant_name}
        ).encode()

    # ------------------------------------------------------------------
    # Controller helpers (single-use, atomic)
    # ------------------------------------------------------------------
    @api.model
    def _by_token(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([("token", "=", token)], limit=1)

    def _claim_for_stream(self):
        """Atomically move ready->streaming and stamp used_at, so a second click
        on the same link loses the race and gets nothing. Returns True if this
        call won the claim."""
        self.ensure_one()
        self.env.cr.execute(
            "UPDATE saas_backup_download SET state='streaming', used_at=now(), "
            "state_since=now() WHERE id=%s AND state='ready' AND used_at IS NULL "
            "RETURNING id", (self.id,))
        won = bool(self.env.cr.fetchone())
        self.env.cr.commit()
        return won

    def _finish_stream(self, byte_count, ok):
        self.ensure_one()
        self._set_state("done" if ok else "failed",
                        byte_count=byte_count,
                        error=None if ok else "stream interrupted")
        self._cleanup_staging()
        self.env.cr.commit()

    # ------------------------------------------------------------------
    # Expiry + watchdog
    # ------------------------------------------------------------------
    @api.model
    def _expire_stale(self):
        now = fields.Datetime.now()
        # expired links (never used, or ready-but-timed-out): flip + clean staging
        stale = self.search(["|", ("expires_at", "<", now),
                             "&", ("state", "in", ("pending", "preparing")),
                             ("state_since", "<", now - timedelta(
                                 minutes=self._STUCK_DEFAULT_MIN))])
        stale = stale.filtered(lambda r: r.state in self.ACTIVE_STATES)
        for rec in stale:
            rec._set_state("expired")
            rec._cleanup_staging()
            self.env.cr.commit()
