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

    # The plan every new tenant gets unless one is chosen explicitly. Set this
    # to a plan whose template is built (e.g. Basic / tpl_basic) so tenants
    # created from the dashboard clone in seconds instead of being built from
    # scratch. Leave empty to build every new tenant from scratch by default.
    default_plan_id = fields.Many2one(
        "saas.plan",
        string="Default Plan",
        help="New tenants use this plan (and its template) unless another is "
             "chosen. Pick a plan with a built template for instant provisioning.",
    )
    # Default storage allowance a new tenant gets. It is a soft quota: it drives
    # the used/quota gauge and the "near full / full" upsell signal on the
    # dashboard -- Postgres does not hard-block writes at this size.
    default_quota_gb = fields.Float(
        string="Default Storage Quota (GB)", default=5.0,
        help="Storage allowance given to a new tenant. Shown as a used/quota "
             "gauge; when a tenant nears it, that is the signal to sell an "
             "upgrade. Editable per tenant.")

    # ------------------------------------------------------------------
    # Pricing — the monthly invoice is simply the storage you ALLOCATE to a
    # tenant times a per-GB rate:
    #   invoice = tenant.quota_gb * price_per_gb
    # Sell a tenant a size (its quota), charge quota * rate; raise the quota when
    # they need more and the invoice goes up with it.
    # ------------------------------------------------------------------
    price_currency = fields.Char(
        string="Currency", default="EGP",
        help="Currency label shown next to computed prices (e.g. EGP, USD).")
    price_per_gb = fields.Float(
        string="Price / GB / month", default=100.0,
        help="Rate charged per GB. The tenant's monthly invoice is its storage "
             "quota (GB) times this rate. Set it from your market/cost rate.")

    @api.model
    def _get(self):
        """Return the single config record, creating it on first call."""
        cfg = self.search([], limit=1)
        if not cfg:
            cfg = self.sudo().create({})
        return cfg
