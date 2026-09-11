import json
import logging
import os

import odoo
from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# The host-side manifest generator writes this file into a root-owned status
# directory inside the Odoo data_dir (the filestore mount). The container can
# READ it but never WRITE it (root:root 0755 dir, 0644 file), so a compromised
# Odoo cannot falsify backup status. B2 credentials never enter a container --
# see BACKUP_MANIFEST.md.
MANIFEST_REL_PATH = os.path.join(".backup_status", "backup_manifest.json")


def _parse_dt(value):
    """ISO '2026-09-10T02:30:00Z' -> naive-UTC 'YYYY-MM-DD HH:MM:SS' for Odoo."""
    if not value:
        return False
    v = value.replace("T", " ").replace("Z", "")
    return v.split(".")[0].split("+")[0].strip()


class SaasBackup(models.Model):
    _name = "saas.backup"
    _description = "SaaS Tenant Backup (registry)"
    _order = "backup_date desc, id desc"

    # One registry row per backup. ref_key is the manifest's stable natural key,
    # so a sync updates in place instead of duplicating.
    ref_key = fields.Char(required=True, index=True, copy=False)
    tenant_id = fields.Many2one(
        "saas.tenant", string="Tenant", ondelete="cascade", index=True)
    tenant_name = fields.Char(required=True, index=True)
    backup_date = fields.Datetime(required=True, index=True)
    source = fields.Selection(
        [("nightly", "Nightly"), ("manual", "Manual"), ("pre_drop", "Pre-drop safety")],
        required=True, default="nightly")

    # Float, not Integer: a large tenant dump easily exceeds the 2 GB int range.
    db_bytes = fields.Float("Database Size (bytes)")
    fs_bytes = fields.Float("Filestore Size (bytes)")
    size_bytes = fields.Float("Total Size", compute="_compute_size", store=True)
    size_display = fields.Char("Size", compute="_compute_size")
    has_filestore = fields.Boolean("Has Filestore")

    reached_b2 = fields.Boolean("Off-site (B2)")
    restic_snapshot_id = fields.Char("Restic Snapshot")
    restic_repo = fields.Selection(
        [("daily", "Daily (prunable)"), ("locked", "Locked (immutable)")],
        string="B2 Repo")
    # No host path is stored or shown: the manifest (container-readable) carries
    # none, and the backup / restore / download operations in later sections
    # resolve the on-disk file host-side from ref_key -- the container never
    # supplies a path.

    _ref_key_uniq = models.Constraint(
        "UNIQUE (ref_key)", "Duplicate backup registry entry.")

    @api.depends("db_bytes", "fs_bytes")
    def _compute_size(self):
        for rec in self:
            total = (rec.db_bytes or 0.0) + (rec.fs_bytes or 0.0)
            rec.size_bytes = total
            rec.size_display = self._human(total)

    @staticmethod
    def _human(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
            n /= 1024.0

    # ------------------------------------------------------------------
    # Population from the host-generated manifest.
    #
    # Read-only registry: the module never talks to B2 or restic. A host-side
    # generator (BACKUP_MANIFEST.md) — the only thing holding the B2 creds —
    # writes backup_manifest.json into the data_dir; we just read it. If it is
    # missing or stale, the resulting empty/old list is itself the alarm.
    # ------------------------------------------------------------------

    @api.model
    def _manifest_path(self):
        return os.path.join(odoo.tools.config["data_dir"], MANIFEST_REL_PATH)

    @api.model
    def _read_manifest(self):
        path = self._manifest_path()
        if not os.path.exists(path):
            _logger.warning("SaaS: backup manifest not found at %s", path)
            return None
        try:
            with open(path, "r") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            _logger.exception("SaaS: cannot parse backup manifest %s", path)
            return None

    @api.model
    def _sync_backups(self):
        """Rebuild the registry from the manifest. Runs as superuser (cron);
        the registry is read-only to users. Missing manifest -> leave as-is."""
        self.env["saas.tenant"]._assert_control_plane()
        manifest = self._read_manifest()
        if manifest is None:
            return
        Backup = self.sudo()
        Tenant = self.env["saas.tenant"].sudo()
        tenant_ids = {t.name: t.id for t in Tenant.search([])}
        seen = set()
        for e in manifest.get("backups", []):
            name = e.get("tenant")
            date = _parse_dt(e.get("date"))
            key = e.get("id") or ("%s-%s-%s" % (e.get("source"), name, e.get("date")))
            if not (name and date and key):
                continue
            seen.add(key)
            vals = {
                "ref_key": key,
                "tenant_name": name,
                "tenant_id": tenant_ids.get(name),
                "backup_date": date,
                "source": e.get("source") or "nightly",
                "db_bytes": e.get("db_bytes") or 0.0,
                "fs_bytes": e.get("fs_bytes") or 0.0,
                "has_filestore": bool(e.get("has_filestore")),
                "reached_b2": bool(e.get("reached_b2")),
                "restic_snapshot_id": e.get("restic_snapshot_id") or False,
                "restic_repo": e.get("restic_repo") or False,
            }
            rec = Backup.search([("ref_key", "=", key)], limit=1)
            if rec:
                rec.write(vals)
            else:
                Backup.create(vals)
        # Drop rows the manifest no longer lists (expired / pruned backups).
        obsolete = (Backup.search([("ref_key", "not in", list(seen))])
                    if seen else Backup.search([]))
        obsolete.unlink()

        # Stash run-level health for saas.tenant.backup_state.
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("saas_backup.last_ok", manifest.get("last_backup_ok") or "")
        ICP.set_param("saas_backup.last_failed", manifest.get("last_backup_failed") or "")
        ICP.set_param("saas_backup.failed_tenants",
                      json.dumps(manifest.get("failed_tenants") or []))
        ICP.set_param("saas_backup.generated_at", manifest.get("generated_at") or "")

        # backup_state is stored (so it can be filtered on). It recomputes on
        # backup changes via @api.depends, but 48h-staleness is time-based, so
        # force a recompute for every tenant each run (every 30 min).
        Tenant.search([])._compute_backup_state()
        self.env.cr.commit()
        _logger.info("SaaS: backup registry synced (%d entries)", len(seen))

    @api.model
    def _cron_sync_backups(self):
        self._sync_backups()

    def action_refresh(self):
        """Manual 'Refresh from host' for the Backups view."""
        self.env["saas.tenant"]._check_developer_group()
        self._sync_backups()
        return {
            "type": "ir.actions.act_window",
            "res_model": "saas.backup",
            "view_mode": "list,form",
            "name": "Backups",
            "target": "current",
        }
