from odoo import api, models


class IrModuleModuleSaas(models.Model):
    """Search ir.module.module by technical name as well as display name.

    The stock _name_search only matches shortdesc (the human label). When an
    operator types the technical name (e.g. 'ica_web_responsive', 'sale',
    'account') in the Default Apps selector it gets no results. This override
    adds an OR on the name field so both match.
    """

    _inherit = "ir.module.module"

    @api.model
    def _name_search(self, name="", domain=None, operator="ilike",
                     limit=100, order=None):
        domain = list(domain or [])
        if name:
            domain = [
                "|",
                ("name", operator, name),
                ("shortdesc", operator, name),
            ] + domain
            return self._search(domain, limit=limit, order=order)
        return super()._name_search(
            name=name, domain=domain, operator=operator,
            limit=limit, order=order,
        )
