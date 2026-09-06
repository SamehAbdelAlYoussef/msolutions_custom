import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaasAuditLog(models.Model):
    _name = "saas.audit.log"
    _description = "SaaS Audit Log"
    _order = "create_date desc, id desc"
    # _log_access = True is the default; create_date / create_uid are the
    # authoritative "when" and "who" columns and are set once by the ORM.

    # --- Who ---
    # Captured when _log() is called, so cron entries record uid=1 (the
    # worker) while user-triggered entries record the authenticated user.
    user_id = fields.Many2one(
        "res.users",
        string="User",
        ondelete="set null",
        index=True,
        readonly=True,
    )

    # --- What ---
    action = fields.Selection(
        [
            ("provision_queued", "Provision Queued"),
            ("provision_ok", "Provision Succeeded"),
            ("provision_failed", "Provision Failed"),
            ("drop_queued", "Drop Queued"),
            ("drop_ok", "Dropped"),
            ("drop_failed", "Drop Failed"),
            ("retry_queued", "Retry Queued"),
            ("shell_query", "Shell Query"),
            ("suspended", "Suspended (over quota)"),
            ("unsuspended", "Restored (within quota)"),
        ],
        string="Action",
        required=True,
        readonly=True,
    )

    # --- Which tenant ---
    # tenant_id goes null when the tenant record is deleted; tenant_name
    # is a snapshot captured at log time so the entry remains readable
    # after the tenant is gone.
    tenant_id = fields.Many2one(
        "saas.tenant",
        string="Tenant",
        ondelete="set null",
        index=True,
        readonly=True,
    )
    tenant_name = fields.Char(
        string="Tenant Name",
        readonly=True,
        help="Recorded at the time of the event; survives tenant deletion.",
    )

    # --- Why ---
    detail = fields.Text(string="Detail", readonly=True)

    # ------------------------------------------------------------------
    # Immutability
    #
    # The CSV restricts write and unlink to nobody (perm_write=0,
    # perm_unlink=0). These overrides add a second layer: sudo() bypasses
    # the CSV but still hits the method, so no caller can tamper with a
    # log entry after it is written.
    # ------------------------------------------------------------------

    def write(self, vals):
        raise UserError(_("Audit log entries are immutable."))

    def unlink(self):
        raise UserError(_("Audit log entries cannot be deleted."))
