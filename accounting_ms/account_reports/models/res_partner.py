# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
``res.partner`` extensions that Enterprise ``account_reports`` provided.

``account_followup`` overrides :meth:`_get_followup_responsible` and calls
``super()``; without this base implementation the Payment Reminder mail template
fails to render during installation.  The customer-statement / follow-up entry
points are kept so the ported buttons resolve.
"""

from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _get_followup_responsible(self, multiple_responsible=False):
        """Default follow-up responsible.

        Returns ``self.env.user``; ``account_followup`` refines this using the
        partner's salesperson, invoice user and company settings.
        """
        if multiple_responsible:
            return self.env.user
        return self.env.user

    def _get_partner_account_report_attachment(self, report, options=None):
        """Render *report* for this partner and store it as an attachment."""
        self.ensure_one()
        if self.lang:
            report = report.with_context(lang=self.lang)
        if not options:
            options = report.get_options({
                "partner_ids": (self | self.commercial_partner_id).ids,
                "unfold_all": len(self.ids) == 1,
            })
        export = report.export_to_pdf(options) if hasattr(report, "export_to_pdf") else None
        if not export:
            return self.env["ir.attachment"]
        return self.env["ir.attachment"].create([{
            "name": f"{self.name} - {export.get('file_name', 'report.pdf')}",
            "res_model": self._name,
            "res_id": self.id,
            "type": "binary",
            "raw": export.get("file_content", b""),
            "mimetype": "application/pdf",
        }])

    def open_customer_statement(self):
        return self._open_account_report("account_reports.customer_statement_report")

    def open_follow_up_report(self):
        return self._open_account_report("account_reports.followup_report")

    def _open_account_report(self, report_xmlid):
        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            return False
        return {
            "type": "ir.actions.client",
            "tag": "account_report",
            "name": report.display_name,
            "params": {
                "options": {
                    "partner_ids": (self | self.commercial_partner_id).ids,
                    "unfold_all": len(self.ids) == 1,
                },
                "ignore_session": True,
            },
            "context": {"report_id": report.id},
        }

    def open_partner(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.id,
            "views": [[False, "form"]],
            "view_mode": "form",
            "target": "current",
        }
