from odoo import api, fields, models


class SaasConfig(models.Model):
    _name = "saas.config"
    _description = "SaaS Global Configuration"

    # Singleton — always access via _get().
    # Using a Many2many here rather than a One2many of a wrapper model:
    # ir.module.module already exists, no need for an extra table.
    default_module_ids = fields.Many2many(
        "ir.module.module",
        "saas_config_default_module_rel",
        "config_id",
        "module_id",
        string="Default Modules",
        # installable=True is the manifest flag — it means the module can be
        # installed on some database. Using state would hide modules that are
        # 'uninstallable' on the control plane (missing deps) but are perfectly
        # valid on a full-stack tenant database. No application filter: technical
        # modules like ica_web_responsive must be selectable too.
        domain="[('installable', '=', True)]",
        help="Modules installed automatically in every new tenant database. "
             "Includes apps AND technical modules (themes, web modules, etc.).",
    )

    @api.model
    def _get(self):
        """Return the single config record, creating it on first call."""
        cfg = self.search([], limit=1)
        if not cfg:
            cfg = self.sudo().create({})
        return cfg
