# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
Return / closing models.

Enterprise ``account_reports`` owns the ``account.return`` family.  The ported
modules inherit these models (``account_intrastat``, ``account_loans``) or call
them (``account_accountant``), so the bridge must expose the same model names
and the same public API entry points.
"""

from odoo import api, fields, models

# Enterprise constant imported by account_intrastat.
LIMIT_CHECK_ENTRIES = 40

_CYCLE_SELECTION = [
    ("fixed_assets", "Fixed Assets"),
    ("treasury_financing", "Treasury & Financing"),
    ("inventory", "Inventory"),
    ("receivables_payables", "Receivables & Payables"),
    ("equity", "Equity"),
    ("taxes", "Taxes"),
    ("other", "Other"),
]


class AccountReturnType(models.Model):
    _name = "account.return.type"
    _description = "msolutions Account Return Type"
    _order = "name"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(string="Code")
    report_id = fields.Many2one(
        "account.report", string="Report", ondelete="cascade",
        help="Report whose data feeds this return.",
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    periodicity = fields.Selection(
        [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly")],
        string="Periodicity", default="monthly",
    )
    states_workflow = fields.Selection(
        [("generic_state_open", "Open"),
         ("generic_state_review_submit", "Reviewed"),
         ("generic_state_submitted", "Submitted")],
        string="Workflow", default="generic_state_open",
    )
    return_ids = fields.One2many("account.return", "type_id", string="Returns")
    active = fields.Boolean(default=True)

    @api.depends("report_id")
    def _compute_states_workflow(self):
        for return_type in self:
            if not return_type.states_workflow:
                return_type.states_workflow = "generic_state_open"

    def _generate_all_returns(self, date_from=None, date_to=None):
        """Create the missing returns for this type.

        Kept intentionally side-effect free when called without dates so it can
        safely be invoked from cron or from tests that patch it.
        """
        created = self.env["account.return"]
        if not date_from or not date_to:
            return created
        for return_type in self:
            created |= self.env["account.return"].create({
                "type_id": return_type.id,
                "date_from": date_from,
                "date_to": date_to,
                "company_id": return_type.company_id.id or self.env.company.id,
            })
        return created


class AccountReturn(models.Model):
    _name = "account.return"
    _description = "msolutions Account Return"
    _order = "date_from desc, id desc"

    name = fields.Char(string="Reference", compute="_compute_name", store=True)
    type_id = fields.Many2one("account.return.type", string="Return Type",
                              required=True, ondelete="cascade")
    reporting_period = fields.Char(string="Period")
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    company_id = fields.Many2one("res.company", string="Company",
                                 default=lambda self: self.env.company.id)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(
        [("open", "Open"), ("reviewed", "Reviewed"), ("submitted", "Submitted")],
        string="Status", default="open",
    )
    check_ids = fields.One2many("account.return.check", "return_id", string="Checks")
    check_count = fields.Integer(compute="_compute_check_count")
    report_id = fields.Many2one(related="type_id.report_id")
    # Referenced by the account_intrastat kanban arch (`invisible=` expression).
    is_completed = fields.Boolean(string="Is Completed", default=False)
    active = fields.Boolean(default=True)

    def action_reset_tax_return_common(self):
        """Reset the return to its initial state (kanban dropdown anchor)."""
        self.write({"state": "open", "is_completed": False})
        return True

    def action_reset_2_states(self):
        """Second-level reset, overridden by account_intrastat."""
        return self.action_reset_tax_return_common()

    @api.depends("type_id", "date_from", "date_to")
    def _compute_name(self):
        for record in self:
            label = record.type_id.name or "Return"
            if record.date_from and record.date_to:
                record.name = f"{label} ({record.date_from} - {record.date_to})"
            else:
                record.name = label

    def _compute_check_count(self):
        for record in self:
            record.check_count = len(record.check_ids)

    def _get_closing_report_options(self):
        """Options used to compute the closing report of this return."""
        self.ensure_one()
        if not self.report_id:
            return {}
        return self.report_id.get_options({
            "date": {
                "date_from": self.date_from,
                "date_to": self.date_to,
                "filter": "custom",
                "mode": "range",
            },
        })

    def _run_checks(self, check_codes_to_ignore=None):
        """Run the audit checks attached to this return type.

        Community replacement: returns the checks already materialised on the
        record instead of generating them from the report engine.
        """
        self.ensure_one()
        check_codes_to_ignore = check_codes_to_ignore or []
        checks = []
        for template in self.env["account.return.check.template"].search(
                [("cycle", "!=", False)]):
            if template.code in check_codes_to_ignore:
                continue
            checks.append({
                "code": template.code,
                "name": template.name,
                "message": template.description or "",
                "result": "todo",
            })
        return checks

    @api.model
    def action_open_tax_return_view(self, additional_return_domain=None):
        """Open the return list (used by account_accountant lock-date wizard)."""
        domain = additional_return_domain or []
        return {
            "type": "ir.actions.act_window",
            "name": "Returns",
            "res_model": "account.return",
            "view_mode": "kanban,list,form",
            "domain": domain,
            "context": {"create": False},
        }


class AccountReturnCheck(models.Model):
    _name = "account.return.check"
    _description = "msolutions Account Return Check"
    _order = "return_id, id"

    return_id = fields.Many2one("account.return", string="Return", ondelete="cascade")
    template_id = fields.Many2one("account.return.check.template", string="Template")
    name = fields.Char(string="Name")
    message = fields.Text(string="Message")
    code = fields.Char(string="Code")
    result = fields.Selection(
        [("todo", "To Do"), ("reviewed", "Reviewed"), ("anomaly", "Anomaly")],
        string="Result", default="todo",
    )
    records_count = fields.Integer(string="Records")
    records_model = fields.Many2one("ir.model", string="Records Model")
    action = fields.Reference(
        selection=[("ir.actions.act_window", "Action Window"),
                   ("ir.actions.client", "Client Action")],
        string="Action",
    )


class AccountReturnCheckTemplate(models.Model):
    _name = "account.return.check.template"
    _description = "msolutions Account Return Check Template"
    _order = "cycle, sequence, id"

    name = fields.Char(string="Name", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    type = fields.Selection(
        [("check", "Check"), ("todo", "To Do")], string="Type", default="check",
    )
    model = fields.Char(string="Model", help="Optional model the check inspects.")
    cycle = fields.Selection(_CYCLE_SELECTION, string="Cycle")
    return_type = fields.Many2one("account.return.type", string="Return Type")
    code = fields.Char(string="Code")
    action_id = fields.Many2one("ir.actions.actions", string="Action")
    additional_action_params = fields.Text(string="Additional Action Params")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
