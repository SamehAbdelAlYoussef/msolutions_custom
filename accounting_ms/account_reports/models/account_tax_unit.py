# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
``account.tax.unit`` is declared by Enterprise ``account_reports`` and is used
as the comodel of ``res.company.account_tax_unit_ids``.  Community does not ship
it, so the bridge provides a functional minimal definition.
"""

from odoo import fields, models


class AccountTaxUnit(models.Model):
    _name = "account.tax.unit"
    _description = "msolutions Tax Unit"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    code = fields.Char(string="Code")
    country_id = fields.Many2one("res.country", string="Country")
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company, required=True,
    )
    main_company_id = fields.Many2one("res.company", string="Main Company")
    company_ids = fields.Many2many("res.company", string="Companies")
    active = fields.Boolean(default=True)
