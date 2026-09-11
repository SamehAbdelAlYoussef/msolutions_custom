# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMsolutionsBridge(TransactionCase):
    """Verify the bridge absorbs the Enterprise symbols the port relies on."""

    def test_account_move_field_patches(self):
        self.assertIn("closing_return_id", self.env["account.move"]._fields)
        self.assertIn("exclude_bank_lines", self.env["account.move.line"]._fields)

    def test_company_field_patches(self):
        for name in (
            "totals_below_sections",
            "account_return_periodicity",
            "account_return_reminder_day",
            "account_tax_return_journal_id",
            "account_revaluation_journal_id",
            "account_tax_unit_ids",
            "account_representative_id",
        ):
            self.assertIn(name, self.env["res.company"]._fields)

    def test_return_models_available(self):
        return_type = self.env["account.return.type"].create({
            "name": "Bridge Test Return",
            "code": "bridge_test",
        })
        closing = self.env["account.return"].create({"type_id": return_type.id})
        self.assertTrue(closing.name)
        action = self.env["account.return"].action_open_tax_return_view()
        self.assertEqual(action["res_model"], "account.return")

    def test_return_check_template_fields(self):
        for name in ("return_type", "cycle", "code", "action_id", "additional_action_params"):
            self.assertIn(name, self.env["account.return.check.template"]._fields)

    def test_reports_engine_available(self):
        report = self.env.ref("account_reports.balance_sheet")
        options = report.get_options({})
        self.assertIn("columns", options)
        self.assertIn("date", options)
        self.assertEqual(report.custom_handler_model_name, "account.report.custom.handler")

    def test_line_id_roundtrip(self):
        report = self.env["account.report"]
        line_id = report._get_generic_line_id("res.partner", 7, markup="group")
        self.assertEqual(report._get_model_info_from_id(line_id), ("res.partner", 7))
        self.assertEqual(report._get_res_id_from_line_id(line_id, "res.partner"), 7)

    def test_fiscal_category_models_available(self):
        self.assertIn("account.fiscal.category", self.env)
        self.assertIn("account.account.fiscal.rate", self.env)

    def test_bank_statement_import_hooks(self):
        journal = self.env["account.journal"]
        self.assertIsInstance(journal._get_bank_statements_available_import_formats(), list)
