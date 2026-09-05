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
        # No domain: ir.module.module.installable is not a stored field in
        # this Odoo version, and filtering by state hides modules that are
        # 'uninstallable' on the control plane but valid on a tenant DB.
        # The many2many_tags widget has autocomplete search — type a name to find.
        domain="[]",
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
