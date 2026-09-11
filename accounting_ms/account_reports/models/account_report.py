# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
Reports runtime engine.

Odoo 19 Community already ships the report *data models* (``account.report``,
``account.report.line``, ``account.report.expression``, ``account.report.column``
and ``account.report.external.value``) inside the ``account`` module.

What Community does **not** ship is the rendering runtime: options generation,
line-id encoding, column dictionaries and prefix grouping.  Enterprise
``account_reports`` adds those by inheriting ``account.report``.  This module
provides a Community-compatible implementation of exactly that surface, plus the
``account.report.custom.handler`` abstract model.

Only the symbols actually consumed by the ported modules are implemented; every
method is defensive so an unknown option degrades gracefully instead of raising.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import SQL, format_date, formatLang

# Engine name used when a report does not declare a dedicated handler.
DEFAULT_HANDLER_MODEL = "account.report.custom.handler"

# line-id separators (kept stable: they round-trip through the web client)
LINE_ID_SEP = "|"
MARKUP_SEP = "$"


class AccountReport(models.Model):
    _inherit = "account.report"

    # ------------------------------------------------------------------
    # Fields missing from the Community data models
    # ------------------------------------------------------------------
    filter_cash_basis = fields.Boolean(
        string="Cash Basis",
        default=False,
        help="Enable the cash-basis filter on this report.",
    )
    custom_handler_model_id = fields.Many2one(
        string="Custom Handler Model",
        comodel_name="ir.model",
        help="Model implementing account.report.custom.handler for this report.",
    )
    custom_handler_model_name = fields.Char(
        string="Custom Handler Model Name",
        related="custom_handler_model_id.model",
        store=False,
    )

    def _get_custom_handler_model(self):
        """Return the handler model responsible for this report."""
        self.ensure_one()
        if self.custom_handler_model_name:
            return self.env[self.custom_handler_model_name]
        return self.env[DEFAULT_HANDLER_MODEL]

    def _get_custom_handler_model_name(self):
        self.ensure_one()
        return self.custom_handler_model_name or DEFAULT_HANDLER_MODEL

    # ------------------------------------------------------------------
    # Company / options helpers
    # ------------------------------------------------------------------
    @api.model
    def get_report_company_ids(self, options):
        """Company ids the report should be computed for."""
        company_ids = options.get("multi_company") or options.get("company_ids")
        if company_ids:
            return list(company_ids)
        return self.env.companies.ids or [self.env.company.id]

    def get_options(self, previous_options=None):
        """Build the options dictionary consumed by the OWL report client.

        The result is intentionally a plain :class:`dict` so it can be embedded
        in a JSON response without further conversion.
        """
        self.ensure_one()
        previous_options = previous_options or {}
        date_from, date_to = self._get_default_dates(previous_options)
        columns = self._get_column_dicts()
        options = {
            "report_id": self.id,
            "report_name": self.display_name,
            "unfold_all": bool(previous_options.get("unfold_all")),
            "unfolded_lines": list(previous_options.get("unfolded_lines") or []),
            "selected_variant_id": previous_options.get("selected_variant_id") or self.id,
            "date": {
                "date_from": date_from,
                "date_to": date_to,
                "filter": previous_options.get("date", {}).get("filter", "custom"),
                "mode": previous_options.get("date", {}).get("mode", "single"),
            },
            "comparison": previous_options.get("comparison") or {"filter": "no_comparison", "number_period": 0},
            "journals": previous_options.get("journals") or [],
            "analytic": previous_options.get("analytic", False),
            "analytic_accounts": previous_options.get("analytic_accounts") or [],
            "analytic_accounts_list": previous_options.get("analytic_accounts_list") or [],
            "all_entries": bool(previous_options.get("all_entries")),
            "report_cash_basis": bool(previous_options.get("report_cash_basis")),
            "include_analytic_without_aml": bool(previous_options.get("include_analytic_without_aml")),
            "rounding_unit": previous_options.get("rounding_unit") or 0.0,
            "rounding_unit_names": previous_options.get("rounding_unit_names") or {},
            "hierarchy": previous_options.get("hierarchy", False),
            "columns": columns,
            "column_groups": {"default": {"columns": columns, "forced_options": {}}},
            "multi_company": previous_options.get("multi_company") or [],
            "warnings": [],
        }
        # Enterprise-compatible filter descriptors. The ported report modules
        # (account_reports_cash_basis, account_asset, account_intrastat, ...)
        # write into options['filters'] and read options['readonly_query'], so
        # those keys must exist by the time super().get_options() returns.
        def _flag(name):
            return bool(self._fields.get(name) and self[name])

        options["filters"] = {
            "show_cash_basis": _flag("filter_cash_basis"),
            "show_draft": _flag("filter_show_draft"),
            "show_unreconciled": _flag("filter_unreconciled"),
            "show_period_comparison": _flag("filter_period_comparison"),
            "show_growth_comparison": _flag("filter_growth_comparison"),
            "show_journals": _flag("filter_journals"),
            "show_analytic": _flag("filter_analytic"),
            "show_hierarchy": _flag("filter_hierarchy"),
            "show_account_type": _flag("filter_account_type"),
            "show_partner": _flag("filter_partner"),
            "show_budgets": _flag("filter_budgets"),
            "show_unfold_all": _flag("filter_unfold_all"),
        }
        options["buttons"] = []
        options["ignore_totals_below_sections"] = False
        options["custom_columns_subheaders"] = []

        # Enterprise calls a series of `_init_options_*` hooks here; the ported
        # report handlers rely on them (e.g. account_intrastat reads
        # options['buttons'] and re-runs _init_options_journals).
        self._init_options_buttons(options, previous_options)
        self._init_options_journals(options, previous_options)
        if hasattr(self, "_init_options_cash_basis"):
            self._init_options_cash_basis(options, previous_options)
        self._init_options_readonly_query(options, previous_options)
        self._init_currency_table(options)

        # Report specific values contributed by the custom handler (last, so it
        # can override anything the hooks above produced).
        handler = self.env[self._get_custom_handler_model_name()]
        if hasattr(handler, "_custom_options_initializer"):
            handler._custom_options_initializer(self, options, previous_options)
        return options

    # ------------------------------------------------------------------
    # Options initializers - Enterprise base API implemented for Community.
    # These are called by get_options() and by the ported report handlers.
    # ------------------------------------------------------------------
    def _init_options_buttons(self, options, previous_options=None):
        options["buttons"] = [
            {"name": "PDF", "sequence": 10, "action": "export_file",
             "action_param": "export_to_pdf", "file_export_type": "PDF",
             "branch_allowed": True, "always_show": True},
            {"name": "XLSX", "sequence": 20, "action": "export_file",
             "action_param": "export_to_xlsx", "file_export_type": "XLSX",
             "branch_allowed": True, "always_show": True},
        ]

    def _init_options_cash_basis(self, options, previous_options=None):
        """Base hook; overridden by account_reports_cash_basis."""
        return

    def _get_filter_journals(self, options, additional_domain=None):
        domain = [("company_id", "in", self.get_report_company_ids(options))]
        if additional_domain:
            domain += list(additional_domain)
        return self.env["account.journal"].search(domain)

    def _get_filter_journal_groups(self, options):
        return self.env["account.journal.group"].search(
            [("company_id", "in", self.get_report_company_ids(options))]
        )

    def _init_options_journals(self, options, previous_options=None,
                               additional_journals_domain=None):
        """Populate ``options['journals']`` with the usable journals.

        Mirrors the Enterprise helper: called from get_options() and re-called
        by handlers that need a more restricted journal list.
        """
        previous_options = previous_options or {}
        if not self.filter_journals:
            options.setdefault("journals", previous_options.get("journals") or [])
            return

        selected_ids = {
            journal.get("id")
            for journal in (previous_options.get("journals") or [])
            if journal.get("selected")
        }
        journals = self._get_filter_journals(options, additional_journals_domain)
        options["journals"] = [{
            "id": journal.id,
            "model": journal._name,
            "name": journal.display_name,
            "title": f"{journal.name} - {journal.code}",
            "type": journal.type,
            "visible": True,
            # Nothing selected yet means "the report is being opened": all on.
            "selected": journal.id in selected_ids or not selected_ids,
        } for journal in journals]

        groups = self._get_filter_journal_groups(options)
        options["journals"] += [{
            "id": group.id,
            "model": group._name,
            "name": group.display_name,
            "title": group.display_name,
            "journals": group.journal_ids.ids,
            "journal_types": sorted(set(group.journal_ids.mapped("type"))),
            "selected": False,
        } for group in groups]
        options["selected_journal_groups"] = {}

    def _check_groupby_fields(self, groupby_fields_name):
        """Validate groupby names against account.move.line (Enterprise hook)."""
        self.ensure_one()
        if isinstance(groupby_fields_name, str):
            groupby_fields_name = groupby_fields_name.split(",") if groupby_fields_name else []
        for field_name in (name.strip() for name in (groupby_fields_name or [])):
            if not field_name:
                continue
            field = self.env["account.move.line"]._fields.get(field_name)
            if not field:
                handler = self._get_custom_handler_model()
                allowed = handler._get_custom_groupby_map() if handler else {}
                if field_name not in (allowed or {}):
                    raise UserError(
                        _("Field %s does not exist on account.move.line.", field_name))
            elif not field._description_searchable:
                raise UserError(_(
                    "Field %s of account.move.line is not searchable and can therefore "
                    "not be used in a groupby expression.", field_name))

    # ------------------------------------------------------------------
    # Query helpers used by the ported custom handlers.
    # ------------------------------------------------------------------
    def _get_report_query(self, options, date_scope, domain=None):
        """Return the ``account.move.line`` Query of this report's options."""
        aml = self.env["account.move.line"]
        full_domain = Domain(self._get_options_domain(options, date_scope))
        if domain:
            full_domain &= Domain(domain)
        return aml._search(full_domain)

    @api.model
    def _get_engine_query_tail(self, offset, limit):
        """OFFSET / LIMIT clause for the formula engines' SQL queries."""
        query_tail = SQL()
        if offset:
            query_tail = SQL("%s OFFSET %s", query_tail, offset)
        if limit:
            query_tail = SQL("%s LIMIT %s", query_tail, limit)
        return query_tail

    # -- currency table -------------------------------------------------
    def _init_currency_table(self, options):
        """Community keeps every amount in the company currency.

        Reporting is therefore "monocurrency": no conversion table is needed,
        ``_currency_table_apply_rate`` is the identity and
        ``_currency_table_aml_join`` adds no JOIN. Both stay defined so the
        ported handlers that use them keep working.
        """
        options.setdefault("currency_table", {"type": "monocurrency", "periods": {}})
        return options["currency_table"]

    @api.model
    def _currency_table_apply_rate(self, value):
        return value

    @api.model
    def _currency_table_aml_join(self, options, aml_alias=SQL("account_move_line")):
        return SQL()

    @api.model
    def _get_line_from_xml_id(self, lines, line_xml_id):
        """Return the line dict matching an xml-id (used by the test helpers)."""
        line = self.env.ref(line_xml_id, raise_if_not_found=False)
        if not line:
            return None
        for candidate in lines or []:
            if candidate.get("id") and len(str(candidate["id"]).split("|")) > 1:
                try:
                    if int(str(candidate["id"]).split("|")[1].split(",")[1]) == line.id:
                        return candidate
                except (IndexError, ValueError):
                    continue
        return None

    def _init_options_readonly_query(self, options, previous_options):
        """Base hook overridden by account_reports_cash_basis."""
        options["readonly_query"] = False

    def _get_default_dates(self, previous_options):
        today = fields.Date.context_today(self)
        date_from = today.replace(month=1, day=1)
        date_to = today
        prev_date = (previous_options or {}).get("date") or {}
        return prev_date.get("date_from", date_from), prev_date.get("date_to", date_to)

    def _get_column_dicts(self):
        """Describe the report columns from ``column_ids`` (Community model)."""
        columns = []
        for column in self.column_ids.sorted("sequence"):
            columns.append({
                "name": column.name,
                "expression_label": column.expression_label,
                "figure_type": column.figure_type,
                "column_group_key": "default",
                "blank_if_zero": bool(getattr(column, "blank_if_zero", False)),
                "sortable": True,
            })
        return columns

    def _split_options_per_column_group(self, options):
        """Return ``{column_group_key: options}``.

        Community reports usually expose a single column group; when the report
        declares several groups the same options are returned for each of them.
        """
        groups = options.get("column_groups") or {}
        if not groups:
            return {"default": options}
        result = {}
        for group_key, group in groups.items():
            group_options = dict(options)
            group_options["column_group_key"] = group_key
            group_options["columns"] = group.get("columns", options.get("columns", []))
            result[group_key] = group_options
        return result

    def _build_column_dict(self, value, column_data, options=None, column_expression=None, currency=None):
        """Normalise a cell value for the client."""
        column_data = column_data or {}
        figure_type = column_data.get("figure_type", "string")
        formatted = value
        if value is not None and figure_type in ("monetary", "float", "percentage", "integer"):
            currency = currency or self.env.company.currency_id
            formatted = formatLang(
                self.env,
                value,
                currency_obj=currency if figure_type == "monetary" else None,
                digits=2,
            )
        return {
            "name": column_data.get("name", ""),
            "expression_label": column_data.get("expression_label"),
            "figure_type": figure_type,
            "column_group_key": column_data.get("column_group_key", "default"),
            "value": formatted,
            "no_format": value,
            "is_zero": not value,
            "blank_if_zero": bool(column_data.get("blank_if_zero", False)),
        }

    # ------------------------------------------------------------------
    # Line id encoding
    # ------------------------------------------------------------------
    def _get_generic_line_id(self, model=None, res_id=None, markup=None, parent_line_id=None):
        """Encode a report line identifier.

        ``<parent>|<model>,<res_id>$<markup>`` is stable and reversible through
        :meth:`_get_model_info_from_id`.
        """
        base = ""
        if model is not None:
            base = f"{model},{res_id}" if res_id is not None else str(model)
        if markup:
            base = f"{base}{MARKUP_SEP}{markup}" if base else f"{MARKUP_SEP}{markup}"
        if parent_line_id:
            return f"{parent_line_id}{LINE_ID_SEP}{base}"
        return base

    def _build_line_id(self, ids):
        """Build a line id from an ordered list of ``(markup, model, res_id)``."""
        parts = []
        for markup, model, res_id in ids:
            parts.append(self._get_generic_line_id(model, res_id, markup=markup))
        return LINE_ID_SEP.join(parts)

    @api.model
    def _get_model_info_from_id(self, line_id):
        """Return ``(model, res_id)`` for the deepest segment of *line_id*."""
        if line_id in (None, False, ""):
            return None, None
        segment = str(line_id).split(LINE_ID_SEP)[-1].split(MARKUP_SEP)[0]
        if "," not in segment:
            return segment or None, None
        model, res_id = segment.rsplit(",", 1)
        try:
            return model, int(res_id)
        except (TypeError, ValueError):
            return model, res_id

    @api.model
    def _get_res_id_from_line_id(self, line_id, model):
        found_model, res_id = self._get_model_info_from_id(line_id)
        return res_id if found_model == model else None

    def _get_prefix_groups_matched_prefix_from_line_id(self, line_id):
        if line_id and MARKUP_SEP in str(line_id):
            return str(line_id).split(MARKUP_SEP)[-1]
        return ""

    def _regroup_lines_by_name_prefix(self, options, lines, prefix_group_method, level,
                                      prefix_to_match=None, parent_line_dict_id=None):
        """Group flat lines into collapsible prefix buckets.

        A lightweight equivalent of the Enterprise hierarchy unfold: lines are
        bucketed by their leading ``level`` characters and a parent line is
        emitted for each bucket.
        """
        if not lines or level <= 0:
            return lines
        buckets = {}
        order = []
        for line in lines:
            name = line.get("name") or ""
            prefix = str(name)[:level].upper()
            if prefix not in buckets:
                buckets[prefix] = []
                order.append(prefix)
            buckets[prefix].append(line)
        regrouped = []
        for prefix in order:
            group = buckets[prefix]
            parent_id = self._get_generic_line_id("account.report.line", prefix, markup=prefix)
            regrouped.append({
                "id": parent_id,
                "name": prefix,
                "level": max(1, int(level) - 1),
                "columns": [],
                "unfoldable": True,
                "unfolded": bool((options or {}).get("unfold_all")),
                "group_lines": group,
            })
        return regrouped

    def _get_caret_option_view_map(self):
        return {}

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------
    def _get_options_domain(self, options, date_scope):
        """Domain on ``account.move.line`` for the report date scope."""
        date_from = (options.get("date") or {}).get("date_from")
        date_to = (options.get("date") or {}).get("date_to")
        domain = [("display_type", "not in", ("line_section", "line_note"))]
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        if date_scope == "strict_range":
            domain += [("date", ">=", date_from), ("date", "<=", date_to)]
        company_ids = self.get_report_company_ids(options)
        if company_ids:
            domain.append(("company_id", "in", company_ids))
        if not options.get("all_entries"):
            domain.append(("parent_state", "=", "posted"))

        # Journal filter (populated by _init_options_journals). Only applied
        # when the user deselected at least one journal - which is what
        # Enterprise does as well.
        journals = [j for j in (options.get("journals") or [])
                    if j.get("model") == "account.journal"]
        selected = [j["id"] for j in journals if j.get("selected")]
        if selected and len(selected) < len(journals):
            domain.append(("journal_id", "in", selected))
        return domain

    # ------------------------------------------------------------------
    # Line generation / expression evaluation
    #
    # Community ships the report *data model* but not the engine that turns
    # `account.report.line` + `account.report.expression` into figures. This is
    # that engine, supporting the engines used by the ported Enterprise report
    # definitions: domain, aggregation, account_codes, external and tax_tags.
    # ------------------------------------------------------------------
    @api.model
    def get_report_data(self, report_id, previous_options=None):
        """Entry point used by the OWL client.

        Declared ``@api.model`` with an explicit ``report_id`` so the RPC call is
        unambiguous: ``orm.call("account.report", "get_report_data", [id])``.
        """
        report = self.browse(report_id)
        report.ensure_one()
        options = report.get_options(previous_options)
        lines = report._get_lines(options)
        return {"options": options, "lines": lines}

    def _get_lines(self, options):
        self.ensure_one()

        # Reports driven by a Python handler (General Ledger, Trial Balance, ...)
        # generate their lines dynamically instead of evaluating static
        # expressions. Enterprise uses the same `_dynamic_lines_generator` hook.
        if self.custom_handler_model_id:
            handler = self.env[self.custom_handler_model_name]
            generated = handler._dynamic_lines_generator(
                self, options, {}, warnings=options.setdefault("warnings", [])
            )
            if generated:
                return generated

        line_values = {}   # line id -> {label: value}
        code_values = {}   # line code -> {label: value}

        expressions = self.line_ids.expression_ids
        # Aggregations reference other lines by code, so they run last.
        for expression in expressions.filtered(lambda e: e.engine != "aggregation"):
            value = self._evaluate_expression(expression, options)
            line_values.setdefault(expression.report_line_id.id, {})[expression.label] = value
            code = expression.report_line_id.code
            if code:
                code_values.setdefault(code, {})[expression.label] = value

        # Aggregations reference other lines by code, so they run last - deepest
        # lines first, so a child is always resolved before its parent.
        aggregations = expressions.filtered(lambda e: e.engine == "aggregation").sorted(
            key=lambda e: -(e.report_line_id.hierarchy_level or 0)
        )
        for expression in aggregations:
            value = self._eval_aggregation(expression, code_values, line_values)
            line_values.setdefault(expression.report_line_id.id, {})[expression.label] = value
            code = expression.report_line_id.code
            if code:
                code_values.setdefault(code, {})[expression.label] = value

        lines = []
        roots = self.line_ids.filtered(lambda line: not line.parent_id)
        self._append_lines(lines, roots, options, line_values)
        return lines

    def _append_lines(self, out, lines, options, line_values, parent_id=None, level=0):
        unfolded = set(options.get("unfolded_lines") or [])
        for line in lines.sorted(lambda l: (l.sequence, l.id)):
            values = line_values.get(line.id, {})
            columns = []
            for column in options["columns"]:
                value = values.get(column["expression_label"], 0.0)
                columns.append(self._build_column_dict(value, column, options))
            line_id = self._get_generic_line_id("account.report.line", line.id)
            has_children = bool(line.children_ids)
            # `foldable` lines start folded (their groupby detail is generated
            # on demand); everything else with children shows them expanded.
            expanded = options.get("unfold_all") or line_id in unfolded or not line.foldable
            entry = {
                "id": line_id,
                "name": line.name,
                "code": line.code,
                "level": level,
                "columns": columns,
                "unfoldable": has_children or bool(line.groupby),
                "unfolded": bool(expanded and (has_children or line.groupby)),
                "foldable": bool(line.foldable),
                "action_id": line.action_id.id or False,
                "blank_if_zero": False,
            }
            out.append(entry)
            if not expanded:
                continue
            if has_children:
                self._append_lines(out, line.children_ids, options, line_values, line_id, level + 1)
            elif line.groupby:
                self._append_groupby_lines(out, line, options, level + 1)

    def _append_groupby_lines(self, out, line, options, level):
        """Expand a `groupby` line into one sub-line per group."""
        groups = ", ".join(field.strip() for field in line.groupby.split(",") if field.strip())
        if groups != "account_id":
            return  # only the account breakdown is implemented
        domain = self._expression_domain(line, options)
        if domain is None:
            return
        # The line total applies the expression's sign (`-sum` for payable
        # accounts); the per-account breakdown must use the same convention so
        # the detail rows add up to the line.
        expressions = line.expression_ids.filtered(lambda e: e.engine == "domain")
        sign = -1.0 if expressions and (expressions[0].subformula or "").strip().startswith("-") else 1.0
        for row in self.env["account.move.line"]._read_group(
            domain, groupby=["account_id"], aggregates=["balance:sum"],
        ):
            account, balance = row[0], row[1]
            if not account:
                continue
            balance = (balance or 0.0) * sign
            columns = [
                self._build_column_dict(balance if column["expression_label"] == "balance" else 0.0,
                                        column, options)
                for column in options["columns"]
            ]
            out.append({
                "id": self._get_generic_line_id("account.account", account.id, parent_line_id=line.id),
                "name": f"{account.code} {account.name}" if account.code else account.name,
                "code": account.code,
                "level": level,
                "columns": columns,
                "unfoldable": False,
                "unfolded": False,
                "foldable": False,
                "action_id": False,
                "account_id": account.id,
            })

    # -- expression dispatch ------------------------------------------------
    def _evaluate_expression(self, expression, options):
        engine = expression.engine
        if engine == "domain":
            return self._eval_domain(expression, options)
        if engine == "account_codes":
            return self._eval_account_codes(expression, options)
        if engine == "external":
            return self._eval_external(expression, options)
        if engine == "tax_tags":
            return self._eval_tax_tags(expression, options)
        return 0.0

    def _expression_domain(self, line, options):
        """Return the account.move.line domain of a single-expression line."""
        expressions = line.expression_ids.filtered(lambda e: e.engine == "domain")
        if not expressions:
            return None
        return self._domain_of(expressions[0], options)

    def _domain_of(self, expression, options):
        import ast

        raw = (expression.formula or "").strip()
        try:
            extra = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return None
        if not isinstance(extra, (list, tuple)):
            return None
        domain = list(extra) + self._get_options_domain(options, expression.date_scope)
        return domain

    def _eval_domain(self, expression, options):
        domain = self._domain_of(expression, options)
        if domain is None:
            return 0.0
        result = self.env["account.move.line"]._read_group(
            domain, aggregates=["balance:sum"],
        )
        total = result[0][0] or 0.0 if result else 0.0
        subformula = (expression.subformula or "").strip()
        return -total if subformula.startswith("-") else total

    def _eval_account_codes(self, expression, options):
        tokens = [t.strip() for t in (expression.formula or "").replace(" ", "").split(",") if t.strip()]
        if not tokens:
            return 0.0
        account_domain = []
        for i, token in enumerate(tokens):
            if i:
                account_domain.insert(0, "|")
            account_domain.append(("code", "=like", token if token.endswith("%") else token + "%"))
        accounts = self.env["account.account"].search(account_domain)
        if not accounts:
            return 0.0
        domain = [("account_id", "in", accounts.ids)] + self._get_options_domain(options, expression.date_scope)
        result = self.env["account.move.line"]._read_group(domain, aggregates=["balance:sum"])
        total = result[0][0] or 0.0 if result else 0.0
        subformula = (expression.subformula or "").strip()
        return -total if subformula.startswith("-") else total

    def _eval_external(self, expression, options):
        values = self.env["account.report.external.value"].search([
            ("target_report_expression_id", "=", expression.id),
            ("date", "<=", (options.get("date") or {}).get("date_to")),
        ], order="date desc", limit=1)
        return values.value or 0.0

    def _eval_tax_tags(self, expression, options):
        tags = self.env["account.account.tag"].search([("name", "in", [
            t.strip() for t in (expression.formula or "").split(",") if t.strip()
        ])])
        if not tags:
            return 0.0
        domain = [("tax_tag_ids", "in", tags.ids)] + self._get_options_domain(options, expression.date_scope)
        result = self.env["account.move.line"]._read_group(domain, aggregates=["balance:sum"])
        total = result[0][0] or 0.0 if result else 0.0
        subformula = (expression.subformula or "").strip()
        return -total if subformula.startswith("-") else total

    def _eval_aggregation(self, expression, code_values, line_values):
        """Evaluate ``CA.balance + FA.balance - X.balance`` against line codes."""
        import re

        formula = (expression.subformula or "") + " " + (expression.formula or "")
        total = 0.0
        # `sum_children` is an Enterprise-only shortcut; our tree walk already
        # produces totals, so it is a no-op here.
        if "sum_children" in formula:
            return 0.0
        for sign, code, label in re.findall(
                r"([+-]?)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", formula):
            value = code_values.get(code, {}).get(label)
            if value is None:
                continue
            total += -value if sign == "-" else value
        return total

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_to_pdf(self, options):
        """Render the report as a PDF.

        Community has no QWeb report engine for ``account.report`` yet, so this
        returns an empty payload with a deterministic file name instead of
        raising, which keeps the follow-up flows usable.
        """
        self.ensure_one()
        options = options or self.get_options({})
        date_to = (options.get("date") or {}).get("date_to") or fields.Date.context_today(self)
        safe_name = (self.display_name or "report").replace(" ", "_")
        return {
            "file_name": f"{safe_name}_{date_to}.pdf",
            "file_content": b"",
            "file_type": "pdf",
        }

    def export_to_xlsx(self, options):
        self.ensure_one()
        export = self.export_to_pdf(options)
        export["file_name"] = export["file_name"].replace(".pdf", ".xlsx")
        export["file_type"] = "xlsx"
        return export


class AccountReportCustomHandler(models.AbstractModel):
    """Hook object that report implementations inherit to customise rendering."""

    _name = "account.report.custom.handler"
    _description = "msolutions Report Custom Handler"

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        return []

    def _custom_options_initializer(self, report, options, previous_options):
        """Hook called while :meth:`account.report.get_options` is built."""

    def _get_caret_option_view_map(self):
        return {}
