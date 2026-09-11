# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
Test helper kept import-compatible with the Enterprise module.

Several ported modules do
``from odoo.addons.account_reports.tests.common import TestAccountReportsCommon``
at test-collection time.  Providing the same class name means ``--test-enable``
can import those suites instead of raising ``ModuleNotFoundError``.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccountReportsCommon(TransactionCase):
    """Minimal stand-in for the Enterprise report test base class."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id

    # -- helpers used by the ported suites ---------------------------------
    def _generate_options(self, report, date_from, date_to, **kwargs):
        """Build report options for the given date range."""
        options = report.get_options({
            "date": {
                "date_from": date_from,
                "date_to": date_to,
                "filter": "custom",
                "mode": "range",
            },
        })
        for key, value in kwargs.items():
            if isinstance(value, dict) and isinstance(options.get(key), dict):
                options[key].update(value)
            else:
                options[key] = value
        return options

    def _get_column_totals(self, report, options):
        """Placeholder total computation retained for API compatibility."""
        return {}

    def _get_generic_line_id(self, model, res_id, markup=None):
        return self.env["account.report"]._get_generic_line_id(model, res_id, markup=markup)

    def _get_model_info_from_id(self, line_id):
        return self.env["account.report"]._get_model_info_from_id(line_id)
