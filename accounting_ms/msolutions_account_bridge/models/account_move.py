# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
``account.move`` / ``account.move.line`` fields contributed by the absorbed
Enterprise modules (``account_reports``).

* ``account.move.closing_return_id`` is written by the
  ``account_accountant`` lock-date wizard when it opens the related tax return;
* ``account.move.line.exclude_bank_lines`` is consumed by the reports runtime.
"""

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    closing_return_id = fields.Many2one(
        comodel_name="account.return",
        string="Closing Return",
        index="btree_not_null",
        copy=False,
        help="Return this closing entry belongs to.",
    )


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    exclude_bank_lines = fields.Boolean(
        string="Exclude Bank Lines",
        compute="_compute_exclude_bank_lines",
        help="Technical flag used by the reports engine to skip bank suspense lines.",
    )

    @api.depends("journal_id", "account_id")
    def _compute_exclude_bank_lines(self):
        for line in self:
            line.exclude_bank_lines = bool(
                line.journal_id
                and line.journal_id.type in ("bank", "cash")
                and line.account_id
                and line.account_id.account_type in (
                    "asset_cash", "liability_credit_card",
                )
            )
