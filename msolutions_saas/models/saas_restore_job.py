import json
import logging
import os
import shutil
from contextlib import closing
from datetime import timedelta

import psycopg2

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.service import db as odoo_db

_logger = logging.getLogger(__name__)

# Hand-off dirs, relative to the data_dir (shared inode host<->container).
RESTORE_SPOOL_REL = os.path.join(".backup_spool", "restore_requests")
RESTORE_RESULT_REL = os.path.join(".backup_spool", "restore_results")
LOCK_REL = os.path.join(".backup_locks", "global.lock")
STAGING_REL = "restore_staging"          # <data_dir>/restore_staging/<job>/

OLD_RETENTION_DAYS = 7                    # keep the pre-restore copy this long
SCRATCH_RETENTION_HOURS = 24             # a dry-run scratch left for inspection
_STUCK_MIN = 45                          # watchdog: a live restore past this -> reconcile


class SaasRestoreJob(models.Model):
    """Restore a tenant to a chosen backup. The MOST destructive action in the
    module, so it is built on one invariant: nothing is ever deleted during a
    restore -- the current state is renamed and kept, the restored copy is built
    beside the live tenant, and the old copy is dropped only later by the guarded
    deferred cleanup. This model is also the journal: every rename is committed so
    a watchdog can reconcile a dead restore deterministically."""
    _name = "saas.restore.job"
    _description = "Tenant restore (to a chosen backup)"
    _order = "requested_at desc, id desc"

    tenant_id = fields.Many2one("saas.tenant", required=True, ondelete="cascade", index=True)
    tenant_name = fields.Char(required=True, index=True)
    backup_ref = fields.Char(required=True)           # the saas.backup ref_key
    backup_date = fields.Datetime()                   # the backup's own timestamp
    backup_bytes = fields.Float()
    reason = fields.Text(required=True)
    dry_run = fields.Boolean(default=False, index=True)
    requested_by = fields.Many2one("res.users", readonly=True,
                                   default=lambda self: self.env.uid)
    requested_at = fields.Datetime(readonly=True, default=fields.Datetime.now)

    state = fields.Selection([
        ("queued", "Queued"),
        ("safety_backup", "Safety backup"),
        ("assembling", "Assembling backup"),
        ("building", "Building scratch"),
        ("verifying", "Verifying scratch"),
        ("dry_done", "Dry run complete"),
        ("maintenance", "Entering maintenance"),
        ("swapping", "Swapping"),
        ("finalizing", "Finalizing"),
        ("done", "Restored"),
        ("failed", "Failed"),
        ("needs_reconcile", "Needs reconcile"),
    ], default="queued", required=True, index=True)
    state_since = fields.Datetime(readonly=True, default=fields.Datetime.now)
    finished_at = fields.Datetime(readonly=True)
    message = fields.Text(readonly=True)

    # Journal / handles
    safety_backup_job_id = fields.Many2one("saas.backup.job", readonly=True)
    safety_backup_ref = fields.Char(readonly=True)    # the resulting saas.backup ref
    scratch_db = fields.Char(readonly=True)
    old_db = fields.Char(readonly=True)               # noveen__old_TS after swap
    old_fs_rel = fields.Char(readonly=True)
    # rename journal (which swap steps committed) -- the watchdog reads these
    live_db_renamed = fields.Boolean(readonly=True)
    scratch_db_renamed = fields.Boolean(readonly=True)
    live_fs_renamed = fields.Boolean(readonly=True)
    scratch_fs_renamed = fields.Boolean(readonly=True)
    maintenance_on = fields.Boolean(readonly=True)
    old_retained = fields.Boolean(readonly=True)      # old copy kept for cleanup
    old_cleaned_at = fields.Datetime(readonly=True)
    # verify report (record counts current vs backup, for the dry-run screen)
    report = fields.Text(readonly=True)

    ACTIVE_STATES = ("queued", "safety_backup", "assembling", "building",
                     "verifying", "maintenance", "swapping", "finalizing")
    LIVE_TOUCH_STATES = ("maintenance", "swapping", "finalizing")

    # ------------------------------------------------------------------
    @api.model
    def _data_dir(self):
        return odoo.tools.config["data_dir"]

    def _set(self, state, **vals):
        self.write(dict(vals, state=state, state_since=fields.Datetime.now()))

    def _fail(self, msg):
        self._set("failed", message=(msg or "")[:1000], finished_at=fields.Datetime.now())
        self.env.cr.commit()
        _logger.error("SaaS restore %s FAILED: %s", self.id, (msg or "")[:300])

    def _names(self):
        self.ensure_one()
        ts = self.requested_at.strftime("%Y%m%d_%H%M%S")
        return {"scratch": "%s__restore_%s" % (self.tenant_name, ts),
                "old": "%s__old_%s" % (self.tenant_name, ts)}

    def _pg(self):
        """Autocommit connection to 'postgres' for CREATE/ALTER/DROP DATABASE."""
        pg = odoo.sql_db.db_connect("postgres")
        cr = pg.cursor()
        cr._cnx.autocommit = True
        return cr

    def _db_exists(self, name):
        with closing(self._pg()) as cr:
            cr.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
            return bool(cr.fetchone())

    def _kill_conns(self, cr, name):
        """Force EVERY connection to `name` closed so an immediately-following
        `ALTER DATABASE name RENAME` (which needs zero connections and cannot run
        in a txn) succeeds. Order matters: invalidate Odoo's own pooled cursors
        client-side first (Registry.delete + close_db), THEN terminate whatever
        backends survive server-side. `cr` must be the autocommit `_pg()` cursor;
        it terminates in the postgres DB so it never kills itself. Called before
        BOTH renames in the swap and before both undo-renames in rollback -- the
        scratch DB in particular carries a pooled connection opened by verify."""
        odoo.modules.registry.Registry.delete(name)
        odoo.sql_db.close_db(name)
        cr.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                   "WHERE datname=%s AND pid<>pg_backend_pid()", (name,))

    def _rename_db(self, cr, src, dst):
        """ALTER DATABASE src RENAME TO dst, made robust against the one failure
        that bit us: pg_terminate_backend is asynchronous, so a just-signalled
        backend can still hold `src` for a few ms and the rename dies with
        ObjectInUse. So kill connections, try the rename, and on ObjectInUse
        re-kill and retry a handful of times with a short back-off. `cr` is the
        autocommit `_pg()` cursor (RENAME cannot run inside a transaction)."""
        import time
        last = None
        for attempt in range(8):
            self._kill_conns(cr, src)
            if attempt:
                time.sleep(0.5)
            try:
                cr.execute(odoo.tools.SQL("ALTER DATABASE %s RENAME TO %s",
                           odoo.tools.SQL.identifier(src),
                           odoo.tools.SQL.identifier(dst)))
                return
            except psycopg2.errors.ObjectInUse as exc:  # noqa: PERF203
                last = exc
                _logger.warning("rename %s->%s attempt %d: still in use, retrying",
                                src, dst, attempt + 1)
        raise last

    def _lock_fd(self):
        lock_path = os.path.join(self._data_dir(), LOCK_REL)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o660)
        try:
            os.fchmod(fd, 0o660)
        except OSError:
            pass
        return fd

    # ------------------------------------------------------------------
    # Creation (developer, from the confirmation screen)
    # ------------------------------------------------------------------
    @api.model
    def _create_request(self, tenant, backup, reason, dry_run=False):
        if not (reason or "").strip():
            raise UserError(_("A written reason is required to restore."))
        # --- refusals (§k) ---
        if backup.tenant_name != tenant.name:
            raise UserError(_("Cross-tenant restore is refused: that backup is "
                              "for %s, not %s.", backup.tenant_name, tenant.name))
        if tenant.state in ("provisioning", "terminating"):
            raise UserError(_("%s is mid-%s; refuse to restore now.",
                              tenant.name, tenant.state))
        Job = self.env["saas.backup.job"].sudo()
        if Job.search_count([("tenant_id", "=", tenant.id),
                             ("state", "in", list(Job.ACTIVE_STATES))]):
            raise UserError(_("A backup of %s is in progress; try again after it "
                              "finishes.", tenant.name))
        if self.sudo().search_count([("tenant_id", "=", tenant.id),
                                     ("state", "in", list(self.ACTIVE_STATES))]):
            raise UserError(_("A restore of %s is already running.", tenant.name))
        rec = self.sudo().create({
            "tenant_id": tenant.id, "tenant_name": tenant.name,
            "backup_ref": backup.ref_key, "backup_date": backup.backup_date,
            "backup_bytes": backup.size_bytes or 0.0,
            "reason": reason.strip(), "dry_run": dry_run,
        })
        tenant._log("restore_queued",
                    "%s backup %s (%s)%s -- %s" % (
                        "DRY-RUN" if dry_run else "RESTORE", backup.ref_key,
                        backup.backup_date, "", reason.strip()[:120]))
        tenant.message_post(body=_(
            "%(kind)s requested to backup %(ref)s (%(date)s) by %(user)s. Reason: "
            "%(reason)s", kind=("Dry-run restore" if dry_run else "RESTORE"),
            ref=backup.ref_key, date=backup.backup_date, user=self.env.user.name,
            reason=reason.strip()[:200]))
        rec._trigger()
        return rec

    def _trigger(self):
        cron = self.env.ref("msolutions_saas.cron_process_restores",
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------
    # Cron: advance one job per tick (single restore at a time)
    # ------------------------------------------------------------------
    @api.model
    def _cron_process(self):
        self.env["saas.tenant"]._assert_control_plane()
        self.env.cr.execute(
            "SELECT id FROM saas_restore_job WHERE state IN %s "
            "ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1",
            (self.ACTIVE_STATES,))
        row = self.env.cr.fetchone()
        if row:
            self.browse(row[0])._advance()

    def _advance(self):
        self.ensure_one()
        handler = {
            "queued": self._s_preflight,
            "safety_backup": self._s_safety_backup,
            "assembling": self._s_assemble,
            "building": self._s_build,
            "verifying": self._s_verify,
            "swapping": self._s_swap,
        }.get(self.state)
        if not handler:
            return
        try:
            handler()
        except Exception as exc:  # noqa: BLE001
            _logger.exception("SaaS restore %s crashed in %s", self.id, self.state)
            self.env.cr.rollback()
            # A swap that already completed must never be un-done by a late error
            # (the finalize is best-effort, but belt-and-suspenders here too).
            if self.state in ("done", "dry_done"):
                _logger.warning("SaaS restore %s: post-completion error ignored", self.id)
                return
            # A crash in a live-touch stage must go to reconcile, not plain failed.
            if self.state in self.LIVE_TOUCH_STATES:
                self._set("needs_reconcile", message=str(exc)[:500])
                self.env.cr.commit()
            else:
                self._safe_drop_scratch()
                self._fail(str(exc))

    # ------------------------------------------------------------------
    # Stage 1: pre-flight (§a refusals already done at create; disk + integrity)
    # ------------------------------------------------------------------
    def _s_preflight(self):
        tenant = self.tenant_id
        backup = self.env["saas.backup"].sudo().search(
            [("ref_key", "=", self.backup_ref)], limit=1)
        if not backup:
            return self._fail("backup %s is gone from the registry" % self.backup_ref)
        if not backup.reached_b2 and backup.source == "manual":
            return self._fail("backup %s never reached B2; refusing" % self.backup_ref)
        ok, msg = self._disk_preflight(backup)
        if not ok:
            return self._fail(msg)
        # A dry run never swaps, so it needs no safety backup -- keep it fast, it
        # is the common "is this backup good?" case. A real restore takes a safety
        # backup of the current state first (unless there is no live DB to protect).
        if self.dry_run or not self._db_exists(self.tenant_name):
            self._set("assembling")
        else:
            self._set("safety_backup")
        self.env.cr.commit()

    def _disk_preflight(self, backup):
        cur = self.tenant_id._disk_usage(self.tenant_id).get(self.tenant_id.id, {})
        cur_bytes = cur.get("db", 0) + cur.get("files", 0)
        # loaded scratch DB ~ 3x the dump; scratch fs ~ backup fs; staging ~ dump+fs;
        # plus every retained old copy already on disk.
        b = self.env["saas.backup"].sudo().search([("ref_key", "=", backup.ref_key)], limit=1)
        bd, bf = (b.db_bytes or 0), (b.fs_bytes or 0)
        retained = self._retained_bytes()
        need = cur_bytes + 3 * bd + 2 * bf + 5 * (1024 ** 3)
        free = shutil.disk_usage(self._data_dir()).free
        gb = lambda n: "%.1f GB" % (n / float(1024 ** 3))
        if free < need + retained:
            return False, _("Restore refused: needs %(n)s (+ %(r)s already held by "
                            "retained pre-restore copies), %(f)s free.",
                            n=gb(need), r=gb(retained), f=gb(free))
        return True, ""

    @api.model
    def _retained_bytes(self):
        """Disk held by retained __old_ copies (best-effort du on the filestore)."""
        total = 0
        recs = self.sudo().search([("old_retained", "=", True), ("old_cleaned_at", "=", False)])
        for r in recs:
            p = os.path.join(self._data_dir(), r.old_fs_rel or "")
            if r.old_fs_rel and os.path.isdir(p):
                for root, _d, files in os.walk(p):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
        return total

    # ------------------------------------------------------------------
    # Stage 2: safety backup (reuse Backup Now; wait for reached_b2) -- §a
    # ------------------------------------------------------------------
    def _s_safety_backup(self):
        Job = self.env["saas.backup.job"].sudo()
        job = self.safety_backup_job_id
        if not job:
            job = Job.create({"tenant_id": self.tenant_id.id,
                              "tenant_name": self.tenant_name})
            self.write({"safety_backup_job_id": job.id})
            self.tenant_id._trigger_backup_cron()
            self.env.cr.commit()
            return
        if job.state == "done" and job.reached_b2:
            self.write({"safety_backup_ref": "manual-%s-%s" % (self.tenant_name, job.id)})
            self._set("assembling")
            self.env.cr.commit()
        elif job.state == "failed":
            self._fail("safety backup failed (%s) -- refusing to touch the tenant"
                       % (job.message or "")[:200])
        # else: still running -> wait, re-checked next tick

    # ------------------------------------------------------------------
    # Stage 3: assemble the chosen backup into staging (host runner) -- §b
    # ------------------------------------------------------------------
    def _s_assemble(self):
        staging_rel = os.path.join(STAGING_REL, str(self.id))
        # 'restore-' prefix so restore jobs never collide with Section 3 download
        # ids in the shared restore_requests / restore_results spool.
        fn = "restore-%d.json" % self.id
        result_dir = os.path.join(self._data_dir(), RESTORE_RESULT_REL)
        marker = os.path.join(result_dir, fn)
        spool = os.path.join(self._data_dir(), RESTORE_SPOOL_REL, fn)
        if not os.path.exists(marker) and not os.path.exists(spool):
            b = self.env["saas.backup"].sudo().search([("ref_key", "=", self.backup_ref)], limit=1)
            os.makedirs(os.path.dirname(spool), exist_ok=True)
            payload = {"id": self.id, "tenant": self.tenant_name,
                       "staging_rel": staging_rel, "ref_key": self.backup_ref,
                       "source": b.source, "snapshot_id": b.restic_snapshot_id or "",
                       "date": b.backup_date.strftime("%Y-%m-%d") if b.backup_date else ""}
            tmp = spool + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, spool)
            self.env.cr.commit()
            return
        if os.path.exists(marker):
            try:
                res = json.load(open(marker))
            except Exception:  # noqa: BLE001
                res = {"status": "failed", "message": "unreadable result"}
            if res.get("status") == "ok":
                self.write({"scratch_db": self._names()["scratch"]})
                self._set("building")
                self.env.cr.commit()
            else:
                self._fail("assemble failed: %s" % (res.get("message") or "")[:200])
        # else spool present, not yet processed -> wait

    def _staging_abs(self):
        return os.path.join(self._data_dir(), STAGING_REL, str(self.id))

    # ------------------------------------------------------------------
    # Stage 4: build scratch (flock held) -- create, load, own, filestore
    # ------------------------------------------------------------------
    def _s_build(self):
        import fcntl
        fd = self._lock_fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return  # lock busy (nightly/backup/download) -> retry next tick
            self._build_locked()
        finally:
            os.close(fd)

    def _build_locked(self):
        scratch = self.scratch_db or self._names()["scratch"]
        staging = self._staging_abs()
        sql_path = os.path.join(staging, "dump.sql")
        fs_src = os.path.join(staging, "filestore")
        if not os.path.exists(sql_path):
            return self._fail("assembled dump.sql missing")
        # fresh scratch DB
        if self._db_exists(scratch):
            self._drop_db(scratch)
        odoo_db._create_empty_database(scratch)
        # load the --no-owner dump as odoo_provision
        self._psql_load(scratch, sql_path)
        # re-apply the ownership split explicitly (dump is --no-owner) -- §d
        with closing(odoo.sql_db.db_connect(scratch).cursor()) as cr:
            cr.execute(self.env["saas.tenant"]._GRANT_WEB_OWNERSHIP_SQL)
            cr.execute(odoo.tools.SQL("GRANT CONNECT ON DATABASE %s TO odoo_web",
                                      odoo.tools.SQL.identifier(scratch)))
            cr._cnx.commit()
        # scratch filestore
        fs_dst = odoo.tools.config.filestore(scratch)
        if os.path.isdir(fs_dst):
            shutil.rmtree(fs_dst, ignore_errors=True)
        os.makedirs(os.path.dirname(fs_dst), exist_ok=True)
        if os.path.isdir(fs_src):
            shutil.copytree(fs_src, fs_dst)
        else:
            os.makedirs(fs_dst, exist_ok=True)
        self.write({"scratch_db": scratch})
        self._set("verifying")
        self.env.cr.commit()

    def _psql_load(self, dbname, sql_path):
        cfg = odoo.tools.config
        env = dict(os.environ)
        env.setdefault("LC_ALL", "C.UTF-8")   # quiet perl/locale noise on stderr
        if cfg.get("db_password"):
            env["PGPASSWORD"] = cfg["db_password"]
        if cfg.get("db_sslmode"):
            env["PGSSLMODE"] = cfg["db_sslmode"]
        # NOT ON_ERROR_STOP: a dump written by a newer pg_dump can carry SETs an
        # older server rejects (e.g. transaction_timeout on PG<17); those are
        # benign. psql returns non-zero only for catastrophic failures (can't
        # connect, missing file). The VERIFY stage -- core tables, row counts,
        # ownership, filestore -- is the real gate on whether the load is sound.
        args = ["psql", "-q", "-d", dbname, "-f", sql_path]
        if cfg.get("db_host"):
            args += ["--host", cfg["db_host"]]
        if cfg.get("db_port"):
            args += ["--port", str(cfg["db_port"])]
        if cfg.get("db_user"):
            args += ["--username", cfg["db_user"]]
        import subprocess
        p = subprocess.run(args, env=env, capture_output=True, timeout=3600)
        if p.returncode != 0:
            raise UserError(_("psql load failed: %s",
                              (p.stderr or b"").decode(errors="replace")[-400:]))

    # ------------------------------------------------------------------
    # Stage 5: verify scratch (§b) -- ownership assertion is the key one
    # ------------------------------------------------------------------
    def _s_verify(self):
        scratch = self.scratch_db
        rep = self._verify_and_report(scratch)
        if rep.get("error"):
            self._safe_drop_scratch()
            return self._fail("verify failed: %s" % rep["error"])
        self.write({"report": json.dumps(rep, indent=1)})
        if self.dry_run:
            self._set("dry_done", finished_at=fields.Datetime.now(),
                      message="Dry run OK; scratch DB %s left for inspection." % scratch)
            self.tenant_id._log("restore_ok", "DRY-RUN verified backup %s; scratch %s"
                                % (self.backup_ref, scratch))
            self.tenant_id.message_post(body=_(
                "Dry-run restore of backup %(ref)s verified OK. Scratch DB "
                "<code>%(s)s</code> left for inspection.", ref=self.backup_ref, s=scratch))
            self.env.cr.commit()
        else:
            self._set("swapping")
            self.env.cr.commit()

    def _verify_and_report(self, scratch):
        """All raw psycopg on the scratch (+ a peek at the live DB for the
        side-by-side counts). Returns {error?, counts:{table:{scratch,current}}}."""
        tables = ["res_users", "res_partner", "ir_attachment", "res_company",
                  "ir_module_module"]
        rep = {"backup_date": str(self.backup_date), "scratch_db": scratch, "counts": {}}
        try:
            with closing(odoo.sql_db.db_connect(scratch).cursor()) as cr:
                # ownership assertion (§d): no public object owned by a non-odoo_web role
                cr.execute("""SELECT count(*) FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','S','v','m')
                      AND pg_get_userbyid(c.relowner) <> 'odoo_web'""")
                bad = cr.fetchone()[0]
                if bad:
                    return {"error": "%d public objects not owned by odoo_web" % bad}
                for t in tables:
                    cr.execute("SELECT to_regclass(%s)", ("public." + t,))
                    if not cr.fetchone()[0]:
                        return {"error": "core table %s missing" % t}
                    cr.execute(odoo.tools.SQL("SELECT count(*) FROM %s",
                                              odoo.tools.SQL.identifier(t)))
                    rep["counts"].setdefault(t, {})["scratch"] = cr.fetchone()[0]
                cr.execute("SELECT count(DISTINCT store_fname) FROM ir_attachment "
                           "WHERE store_fname IS NOT NULL AND store_fname<>''")
                att = cr.fetchone()[0]
                cr.execute("SELECT value FROM ir_config_parameter WHERE key='database.uuid'")
                if not cr.fetchone():
                    return {"error": "database.uuid missing in the restored dump"}
        except Exception as exc:  # noqa: BLE001
            return {"error": "scratch will not load: %s" % str(exc)[:200]}
        # filestore consistency
        fs_files = sum(len(f) for _r, _d, f in os.walk(odoo.tools.config.filestore(scratch)))
        if fs_files < att:
            return {"error": "filestore incomplete: %d files, %d attachments" % (fs_files, att)}
        # live counts for the side-by-side (best-effort; may be gone if post-drop)
        if self._db_exists(self.tenant_name):
            try:
                with closing(odoo.sql_db.db_connect(self.tenant_name).cursor()) as cr:
                    for t in tables:
                        cr.execute("SELECT to_regclass(%s)", ("public." + t,))
                        if cr.fetchone()[0]:
                            cr.execute(odoo.tools.SQL("SELECT count(*) FROM %s",
                                                      odoo.tools.SQL.identifier(t)))
                            rep["counts"].setdefault(t, {})["current"] = cr.fetchone()[0]
            except Exception:  # noqa: BLE001
                pass
        return rep

    # ------------------------------------------------------------------
    # Stage 6-10: the swap (real restore only) -- flock held through the whole
    # thing so nothing interleaves; the journal is committed after each rename.
    # ------------------------------------------------------------------
    def _s_swap(self):
        """The atomic swap (real restore), held under the flock and done in ONE
        method so nothing interleaves: enter maintenance, wait for the 2s host
        routing sync to serve the 503, terminate connections, rename DB then
        filestore (journaling each rename), finalize sessions, exit maintenance.
        If the process dies mid-way the journal + watchdog roll back."""
        import fcntl
        import time
        fd = self._lock_fd()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return  # nightly/backup/download holds the lock -> retry next tick
            if not self.maintenance_on:
                # flag the tenant, then let update_tenant_list.sh (2s) emit
                # maintenance.conf + reload nginx so the tenant serves the 503.
                self.tenant_id.sudo().write({"restoring": True})
                self.write({"maintenance_on": True})
                self.env.cr.commit()
                time.sleep(5)   # > the 2s routing sync + nginx reload
            self._do_swap()
        finally:
            os.close(fd)

    def _do_swap(self):
        n = self._names()
        scratch, old = self.scratch_db, n["old"]
        have_live = self._db_exists(self.tenant_name)
        with closing(self._pg()) as cr:
            if have_live and not self.live_db_renamed:                 # t1
                self._rename_db(cr, self.tenant_name, old)
                self.write({"live_db_renamed": True, "old_db": old}); self.env.cr.commit()
            if not self.scratch_db_renamed:                            # t2
                self._rename_db(cr, scratch, self.tenant_name)         # verify left a pool here
                self.write({"scratch_db_renamed": True}); self.env.cr.commit()
        cfg = odoo.tools.config
        live_fs = cfg.filestore(self.tenant_name)
        old_fs = cfg.filestore(old)
        scratch_fs = cfg.filestore(scratch)
        if have_live and not self.live_fs_renamed and os.path.isdir(live_fs):    # t3
            os.rename(live_fs, old_fs)
            self.write({"live_fs_renamed": True,
                        "old_fs_rel": os.path.relpath(old_fs, self._data_dir())})
            self.env.cr.commit()
        if not self.scratch_fs_renamed and os.path.isdir(scratch_fs):           # t4
            os.rename(scratch_fs, live_fs)
            self.write({"scratch_fs_renamed": True}); self.env.cr.commit()
        # --- POINT OF NO RETURN reached: the tenant DB and filestore now hold
        #     the restored copy. Everything below is cosmetic (log operators out,
        #     mark done, audit, chatter) and MUST NOT be able to flip a completed
        #     restore to failed or trigger a rollback (§j: no row ends with data
        #     lost). So the whole tail is best-effort and never re-raises. ---
        self._finalize_after_swap()

    def _finalize_after_swap(self):
        """Best-effort post-swap steps. Runs AFTER the irreversible renames, so
        any failure here is logged and swallowed -- the restore is already
        complete and the job must still land on 'done'. Called only from
        _do_swap once all four renames have committed."""
        # rotate database.secret so existing sessions are logged out; a failure
        # here is harmless (worst case a stale session lingers until it expires).
        try:
            with closing(odoo.sql_db.db_connect(self.tenant_name).cursor()) as fcr:
                fcr.execute("UPDATE ir_config_parameter SET "
                            "value=md5(random()::text||clock_timestamp()::text) "
                            "WHERE key='database.secret'")
                fcr._cnx.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("restore %s: secret rotation failed (restore complete)", self.id)
        try:
            odoo.modules.registry.Registry.delete(self.tenant_name)
            odoo.sql_db.close_db(self.tenant_name)
        except Exception:  # noqa: BLE001
            _logger.exception("restore %s: registry invalidate failed (restore complete)", self.id)
        # exit maintenance + mark done. This block matters, so on failure roll the
        # cursor back and force the essential writes (restoring off, state done).
        try:
            self.tenant_id.sudo().write({"restoring": False})
            self.write({"maintenance_on": False, "old_retained": bool(self.old_db)})
            self._set("done", finished_at=fields.Datetime.now(),
                      message="Restored to backup %s (%s)." % (self.backup_ref, self.backup_date))
            self.env.cr.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("restore %s: could not mark done cleanly; forcing", self.id)
            self.env.cr.rollback()
            try:
                self.tenant_id.sudo().write({"restoring": False})
                self._set("done", finished_at=fields.Datetime.now(),
                          message="Restored (finalize partly failed; see log).")
                self.env.cr.commit()
            except Exception:  # noqa: BLE001
                _logger.exception("restore %s: FORCE-done failed", self.id)
        # audit + chatter, best-effort -- never un-do a completed restore.
        try:
            self.tenant_id._log("restore_ok", "restored to backup %s (%s); old kept as %s"
                                % (self.backup_ref, self.backup_date, self.old_db or "-"))
            self.tenant_id.message_post(body=_(
                "RESTORED to backup %(ref)s (%(date)s). Previous state kept as "
                "<code>%(old)s</code> for %(days)s days; safety backup %(sb)s.",
                ref=self.backup_ref, date=self.backup_date, old=self.old_db or "-",
                days=OLD_RETENTION_DAYS, sb=self.safety_backup_ref or "-"))
            self.env.cr.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("restore %s: audit/chatter failed (restore complete)", self.id)

    # ------------------------------------------------------------------
    # Rollback / reconcile (watchdog) -- roll BACK to the pre-restore state
    # ------------------------------------------------------------------
    def _rollback(self):
        """Reverse whatever swap renames completed, returning the tenant to its
        pre-restore state. Safe because nothing was dropped."""
        self.ensure_one()
        cfg = odoo.tools.config
        n = self._names(); old = self.old_db or n["old"]; scratch = self.scratch_db
        with closing(self._pg()) as cr:
            # undo t2: tenant(=scratch) -> scratch
            if self.scratch_db_renamed and self._db_exists(self.tenant_name) \
               and not self._db_exists(scratch):
                self._rename_db(cr, self.tenant_name, scratch)
                self.write({"scratch_db_renamed": False})
            # undo t1: old -> tenant
            if self.live_db_renamed and self._db_exists(old) \
               and not self._db_exists(self.tenant_name):
                self._rename_db(cr, old, self.tenant_name)
                self.write({"live_db_renamed": False})
        live_fs = cfg.filestore(self.tenant_name)
        old_fs = cfg.filestore(old); scratch_fs = cfg.filestore(scratch)
        if self.scratch_fs_renamed and os.path.isdir(live_fs) and not os.path.isdir(scratch_fs):
            os.rename(live_fs, scratch_fs); self.write({"scratch_fs_renamed": False})
        if self.live_fs_renamed and os.path.isdir(old_fs) and not os.path.isdir(live_fs):
            os.rename(old_fs, live_fs); self.write({"live_fs_renamed": False})
        odoo.modules.registry.Registry.delete(self.tenant_name)
        odoo.sql_db.close_db(self.tenant_name)
        self.tenant_id.sudo().write({"restoring": False})
        self.write({"maintenance_on": False})
        self.tenant_id._trigger_cron()
        self._set("failed", finished_at=fields.Datetime.now(),
                  message="Rolled back to the pre-restore state.")
        self.tenant_id._log("restore_failed", "rolled back to pre-restore state")
        self.env.cr.commit()

    @api.model
    def _cron_watchdog(self):
        self.env["saas.tenant"]._assert_control_plane()
        deadline = fields.Datetime.now() - timedelta(minutes=_STUCK_MIN)
        stuck = self.search([("state", "in", ("maintenance", "swapping",
                             "finalizing", "needs_reconcile")),
                             ("state_since", "<", deadline)])
        for job in stuck:
            _logger.warning("SaaS restore watchdog reconciling %s (%s)", job.id, job.state)
            try:
                job._rollback()
            except Exception:  # noqa: BLE001
                _logger.exception("SaaS restore %s rollback FAILED -- manual", job.id)
                job._set("needs_reconcile", message="rollback failed -- MANUAL")
                self.env.cr.commit()

    # ------------------------------------------------------------------
    # Deferred cleanup -- THE ONLY CODE THAT DELETES. Verify before dropping.
    # ------------------------------------------------------------------
    @api.model
    def _cron_cleanup(self):
        self.env["saas.tenant"]._assert_control_plane()
        # dry-run scratch DBs past their short TTL
        for job in self.search([("dry_run", "=", True), ("state", "=", "dry_done"),
                                ("state_since", "<", fields.Datetime.now()
                                 - timedelta(hours=SCRATCH_RETENTION_HOURS))]):
            job._safe_drop_scratch()
            job.write({"message": (job.message or "") + " [scratch cleaned]"})
            self.env.cr.commit()
        # retained pre-restore old copies past 7 days -- GUARDED
        # Key off old_retained -- the authoritative "a swap completed and left
        # this pre-restore copy" flag (a rolled-back job never sets it). We do
        # NOT filter on state='done': a swap that finished but whose cosmetic
        # tail failed can land on 'failed' yet still own a real old copy, and
        # that copy must still be cleaned. _safe_to_drop_old is the true gate.
        cutoff = fields.Datetime.now() - timedelta(days=OLD_RETENTION_DAYS)
        for job in self.search([("old_retained", "=", True), ("old_cleaned_at", "=", False),
                                ("old_db", "!=", False), ("finished_at", "<", cutoff)]):
            ok, why = job._safe_to_drop_old()
            if not ok:
                _logger.error("SaaS restore cleanup REFUSED to drop %s: %s",
                              job.old_db, why)
                self.env["saas.tenant"]._restore_alert(
                    "Refused to drop pre-restore copy %s: %s" % (job.old_db, why))
                continue
            job._drop_old()
            self.env.cr.commit()

    def _safe_to_drop_old(self):
        """Both checks must pass AT THIS MOMENT (§ addition 1)."""
        self.ensure_one()
        tenant = self.tenant_id
        # 1. live tenant of that name exists, resolves 200, working DB
        if tenant.state != "active" or not self._db_exists(self.tenant_name):
            return False, "live tenant not active / no DB"
        if tenant.reachability_state != "ok":
            return False, "live tenant does not resolve (reachability=%s)" % tenant.reachability_state
        try:
            with closing(odoo.sql_db.db_connect(self.tenant_name).cursor()) as cr:
                cr.execute("SELECT 1 FROM res_users LIMIT 1")
                if not cr.fetchone():
                    return False, "live DB query returned nothing"
        except Exception as exc:  # noqa: BLE001
            return False, "live DB not queryable: %s" % str(exc)[:120]
        # 2. the safety backup for this restore reached B2
        if self.safety_backup_ref:
            b = self.env["saas.backup"].sudo().search(
                [("ref_key", "=", self.safety_backup_ref)], limit=1)
            if not (b and b.reached_b2):
                return False, "safety backup %s not confirmed in B2" % self.safety_backup_ref
        return True, ""

    def _drop_old(self):
        self.ensure_one()
        if self.old_db and self._db_exists(self.old_db):
            self._drop_db(self.old_db)
        old_fs = os.path.join(self._data_dir(), self.old_fs_rel or "")
        if self.old_fs_rel and os.path.isdir(old_fs):
            shutil.rmtree(old_fs, ignore_errors=True)
        self.write({"old_retained": False, "old_cleaned_at": fields.Datetime.now(),
                    "message": (self.message or "") + " [old copy cleaned]"})
        self.tenant_id._log("restore_ok", "pre-restore copy %s cleaned after %sd"
                            % (self.old_db, OLD_RETENTION_DAYS))

    # ------------------------------------------------------------------
    def _safe_drop_scratch(self):
        self.ensure_one()
        if self.scratch_db and self._db_exists(self.scratch_db):
            try:
                self._drop_db(self.scratch_db)
            except Exception:  # noqa: BLE001
                _logger.exception("could not drop scratch %s", self.scratch_db)
        fs = odoo.tools.config.filestore(self.scratch_db) if self.scratch_db else None
        if fs and os.path.isdir(fs):
            shutil.rmtree(fs, ignore_errors=True)
        st = self._staging_abs()
        if os.path.isdir(st):
            shutil.rmtree(st, ignore_errors=True)

    def _drop_db(self, name):
        odoo.modules.registry.Registry.delete(name)
        odoo.sql_db.close_db(name)
        with closing(self._pg()) as cr:
            odoo_db._drop_conn(cr, name)
            cr.execute(odoo.tools.SQL("DROP DATABASE %s",
                                      odoo_db.database_identifier(cr, name)))
