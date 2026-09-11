# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
``res.company`` fields contributed by the absorbed Enterprise modules.

These are relation-only / flag fields used by the return, revaluation and tax
unit features.  They carry no Community implementation, but their *presence* is
required so the ported views, reports and settings screens load.
"""

from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # -- reporting options -------------------------------------------------
    totals_below_sections = fields.Boolean(
        string="Add totals below sections",
        help="Show the total of each section below the section lines.",
    )

    # -- returns -----------------------------------------------------------
    account_return_periodicity = fields.Selection(
        [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly")],
        string="Return Periodicity",
        default="monthly",
    )
    account_return_reminder_day = fields.Integer(
        string="Start from", default=7, required=True,
    )
    account_tax_return_journal_id = fields.Many2one(
        "account.journal", string="Tax Return Journal", check_company=True,
    )
    account_last_return_cron_refresh = fields.Datetime(
        string="Last Return Cron Refresh",
    )

    # -- revaluation -------------------------------------------------------
    account_revaluation_journal_id = fields.Many2one(
        "account.journal", string="Revaluation Journal",
        domain=[("type", "=", "general")], check_company=True,
    )
    account_revaluation_expense_provision_account_id = fields.Many2one(
        "account.account", string="Expense Provision Account", check_company=True,
    )
    account_revaluation_income_provision_account_id = fields.Many2one(
        "account.account", string="Income Provision Account", check_company=True,
    )

    # -- tax units ---------------------------------------------------------
    account_tax_unit_ids = fields.Many2many(
        "account.tax.unit", string="Tax Units",
        help="The tax units this company belongs to.",
    )

    # -- representative ----------------------------------------------------
    account_representative_id = fields.Many2one(
        "res.partner", string="Accounting Firm", check_company=True,
    )
    account_display_representative_field = fields.Boolean(
        string="Display Representative Field",
        compute="_compute_account_display_representative_field",
    )

    @api.depends("account_representative_id")
    def _compute_account_display_representative_field(self):
        for company in self:
            company.account_display_representative_field = bool(company.account_representative_id)
