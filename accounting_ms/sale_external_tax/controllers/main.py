# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""Portal hook: recompute external taxes before the customer sees a quotation."""

from odoo.http import route
from odoo.addons.sale.controllers.portal import CustomerPortal


class CustomerPortalExternalTax(CustomerPortal):

    @route()
    def portal_order_page(self, *args, **kwargs):
        response = super().portal_order_page(*args, **kwargs)
        if 'sale_order' not in response.qcontext:
            return response
        sale_order = response.qcontext['sale_order']
        sale_order.with_company(sale_order.company_id)._get_and_set_external_taxes_on_eligible_records()
        return response
