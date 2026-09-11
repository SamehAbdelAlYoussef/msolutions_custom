# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
General Ledger custom handler.

Enterprise defines the General Ledger as a single `account.report.line` whose
expressions all use the `custom` engine (`_report_custom_engine_general_ledger`)
and lets a Python handler generate one line per account plus its journal items.

This is the Community implementation of that handler. It produces the same
output shape as Enterprise:

    account                 Debit       Credit      Balance
        <journal item>      Debit       Credit      Balance
    ...
    Total                   Debit       Credit      Balance
"""

from odoo import models

# The report line this handler drives (the only line of the report).
GL_LINE_XMLID = "account_reports.general_ledger_custom_engine_line"


class AccountGeneralLedgerReportHandler(models.AbstractModel):
    _name = "account.general.ledger.report.handler"
    _inherit = ["account.report.custom.handler"]
    _description = "General Ledger Report Custom Handler"

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        # The General Ledger is a detail report: it is meaningless with a
        # comparison column, and it always shows its journal items on demand.
        options.setdefault("filters", {})
        options["filters"]["show_period_comparison"] = False

    # ------------------------------------------------------------------
    # Line generation
    # ------------------------------------------------------------------
    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals=None, warnings=None):
        domain = report._get_options_domain(options, "strict_range")
        columns = options.get("columns") or []
        unfolded = set(options.get("unfolded_lines") or [])
        unfold_all = bool(options.get("unfold_all"))

        report_line = self.env.ref(GL_LINE_XMLID, raise_if_not_found=False)
        root_id = report._get_generic_line_id("account.report.line", report_line.id if report_line else 0)

        lines = []
        totals = {"debit": 0.0, "credit": 0.0, "balance": 0.0}

        groups = self.env["account.move.line"]._read_group(
            domain,
            groupby=["account_id"],
            aggregates=["debit:sum", "credit:sum", "balance:sum"],
        )
        for account, debit, credit, balance in groups:
            if not account:
                continue
            debit = debit or 0.0
            credit = credit or 0.0
            balance = balance or 0.0
            totals["debit"] += debit
            totals["credit"] += credit
            totals["balance"] += balance

            line_id = f"{root_id}|account.account,{account.id}"
            expanded = unfold_all or line_id in unfolded
            lines.append({
                "id": line_id,
                "name": f"{account.code} {account.name}" if account.code else account.name,
                "code": account.code,
                "level": 0,
                "columns": self._columns(report, options, columns, {
                    "debit": debit, "credit": credit, "balance": balance,
                }),
                "unfoldable": True,
                "unfolded": expanded,
                "foldable": False,
                "action_id": False,
                "account_id": account.id,
            })
            if not expanded:
                continue

            running = 0.0
            amls = self.env["account.move.line"].search(
                domain + [("account_id", "=", account.id)], order="date, id"
            )
            for aml in amls:
                running += aml.balance
                lines.append({
                    "id": f"{line_id}|account.move.line,{aml.id}",
                    "name": self._aml_name(aml),
                    "level": 1,
                    "columns": self._columns(report, options, columns, {
                        "date": aml.date,
                        "partner_name": aml.partner_id.display_name or "",
                        "amount_currency": self._amount_currency(aml),
                        "debit": aml.debit,
                        "credit": aml.credit,
                        "balance": running,
                    }),
                    "unfoldable": False,
                    "unfolded": False,
                    "foldable": False,
                    "action_id": False,
                    "move_line_id": aml.id,
                })

        if lines:
            lines.append({
                "id": f"{root_id}|total",
                "name": "Total",
                "level": 0,
                "columns": self._columns(report, options, columns, totals),
                "unfoldable": False,
                "unfolded": False,
                "foldable": False,
                "action_id": False,
            })
        return lines

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _aml_name(aml):
        return aml.name or aml.move_id.name or aml.ref or ""

    @staticmethod
    def _amount_currency(aml):
        """Only meaningful when the journal item is in a foreign currency."""
        company_currency = aml.company_id.currency_id
        if aml.currency_id and aml.currency_id != company_currency:
            return aml.amount_currency
        return ""

    def _columns(self, report, options, columns, values):
        """Build the cell list for one line, honouring each column's expression."""
        cells = []
        for column in columns:
            value = values.get(column["expression_label"], "")
            cells.append(report._build_column_dict(value, column, options))
        return cells

    # ------------------------------------------------------------------
    # Custom engine (the static expression of the report line)
    # ------------------------------------------------------------------
    def _report_custom_engine_general_ledger(self, expressions, options, date_scope,
                                             current_groupby, next_groupby, offset=0,
                                             limit=None, warnings=None):
        """Required by the report line's `custom` expressions.

        The lines themselves are produced by `_dynamic_lines_generator`; this
        entry point only has to answer for a single (line, label) pair, which is
        used when the client asks for one expression in isolation.
        """
        return {expression.label: 0.0 for expression in expressions}
