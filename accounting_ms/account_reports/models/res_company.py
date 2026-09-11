# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
``res.company`` methods contributed by Enterprise ``account_reports``.

``accountant`` (post-init hook and ``account.chart.template`` override) calls
``company._get_tax_closing_journal()`` while loading a chart of accounts, so the
method has to exist on the Community ``res.company`` model.
"""

from odoo import models

# Field is owned by msolutions_account_bridge, which is loaded after this
# module; the guard keeps the method usable even before that field exists.
_JOURNAL_FIELD = "account_tax_return_journal_id"


class ResCompany(models.Model):
    _inherit = "res.company"

    def _get_tax_closing_journal(self):
        """Return (creating it when missing) the tax-closing journal.

        Mirrors the Enterprise behaviour: reuse the configured journal, else the
        parent company's, else an existing ``TAX``/``TRTRN`` journal, else create
        one.
        """
        if _JOURNAL_FIELD not in self._fields:
            return self.env["account.journal"]
        for company in self:
            if company[_JOURNAL_FIELD]:
                continue
            closing_journal = self.env["account.journal"]
            for parent in reversed(company.sudo().parent_ids):
                if parent[_JOURNAL_FIELD]:
                    closing_journal = parent[_JOURNAL_FIELD]
                    break
            if not closing_journal:
                closing_journal = self.env["account.journal"].sudo().search([
                    *self.env["account.journal"]._check_company_domain(company),
                    ("code", "in", ("TAX", "TRTRN")),  # TRTRN kept for compatibility
                    ("type", "=", "general"),
                ], limit=1)
            if not closing_journal:
                closing_journal = self.env["account.journal"].sudo().create([{
                    "name": self.env._("Tax Returns"),
                    "code": "TAX",
                    "type": "general",
                    "company_id": company.id,
                    "currency_id": company.currency_id.id,
                    "show_on_dashboard": True,
                }])
            company[_JOURNAL_FIELD] = closing_journal
        return self[_JOURNAL_FIELD]
