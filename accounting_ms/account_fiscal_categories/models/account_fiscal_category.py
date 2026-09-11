# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""Fiscal category models and report-handler base classes."""

from odoo import api, fields, models


class AccountAccount(models.Model):
    """``account.account`` extension contributed by fiscal categories.

    ``account_fiscal_categories_fleet`` computes ``need_vehicle`` with
    ``@api.depends('account_id.fiscal_category_id')``, so this field must exist
    on the Community ``account.account`` model.
    """

    _inherit = 'account.account'

    fiscal_category_id = fields.Many2one(
        'account.fiscal.category', string='Fiscal Category',
        index=True, ondelete='restrict',
    )


class AccountMoveLine(models.Model):
    """``account.move.line.need_vehicle`` base definition.

    ``account_fiscal_categories_fleet`` overrides :meth:`_compute_need_vehicle`;
    the field itself must be declared here so it also exists when the fleet
    bridge is not installed (``account_asset_fleet`` views reference it).
    """

    _inherit = 'account.move.line'

    need_vehicle = fields.Boolean(
        string='Requires a Vehicle',
        compute='_compute_need_vehicle',
    )

    @api.depends('account_id.fiscal_category_id')
    def _compute_need_vehicle(self):
        for line in self:
            line.need_vehicle = False


class AccountFiscalCategory(models.Model):
    _name = 'account.fiscal.category'
    _description = 'msolutions Fiscal Category'
    _order = 'code, name'

    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(string='Code')
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True,
    )
    account_ids = fields.Many2many(
        'account.account', 'account_fiscal_category_account_rel',
        'fiscal_category_id', 'account_id', string='Accounts',
    )
    active = fields.Boolean(default=True)

    _code_company_uniq = models.Constraint(
        'UNIQUE(code, company_id)',
        'The fiscal category code must be unique per company.',
    )


class AccountAccountFiscalRate(models.Model):
    _name = 'account.account.fiscal.rate'
    _description = 'msolutions Account Fiscal Rate'
    _order = 'date_from desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    account_id = fields.Many2one('account.account', string='Account', required=True, ondelete='cascade')
    category_id = fields.Many2one('account.fiscal.category', string='Fiscal Category', ondelete='cascade')
    # Odoo 19 `account.account` is multi-company through `company_ids`; there is
    # no `company_id` anymore, so this must be a plain field rather than related.
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, index=True,
    )
    rate = fields.Float(string='Rate', digits=(5, 2), default=100.0)
    date_from = fields.Date(string='From')
    date_to = fields.Date(string='To')
    active = fields.Boolean(default=True)

    @api.depends('account_id', 'category_id', 'rate')
    def _compute_name(self):
        for record in self:
            record.name = f"{record.account_id.display_name or ''} - {record.rate:.2f}%"


class AccountFiscalReportHandler(models.AbstractModel):
    """Base handler inherited by fiscal-category report implementations."""

    _name = 'account.fiscal.report.handler'
    _inherit = ['account.report.custom.handler']
    _description = 'msolutions Fiscal Report Handler'

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)
        options.setdefault('custom_display_config', {})
        options['custom_display_config'].setdefault('components', {})

    def _customize_warnings(self, report, options, all_column_groups_expression_totals, warnings):
        return warnings

    def _get_query(self, options, line_dict_id=None):
        """Return the ``(select, from_, where, group_by, order_by, order_by_rate)``
        tuple consumed by the fiscal report sub-handlers."""
        return "", "", "", "", "", ""

    def _get_current_deductible_amount(self, values):
        return values.get('deductible_amount', 0.0)


class AccountDeferredReportHandler(models.AbstractModel):
    """Base handler for deferred expense/revenue reports."""

    _name = 'account.deferred.report.handler'
    _inherit = ['account.report.custom.handler']
    _description = 'msolutions Deferred Report Handler'

    @api.model
    def _get_select(self, options):
        return []

    @api.model
    def _get_grouping_fields_deferred_lines(self, filter_already_generated=False, grouping_field='account_id'):
        return ('account_id',)

    @api.model
    def _get_grouping_fields_deferral_lines(self):
        return ('account_id',)

    @api.model
    def _get_current_key_totals_dict(self, lines_per_key, sign):
        return {}

    @api.model
    def _generate_deferral_entry(self, options):
        """Create the deferral journal entries for the given options.

        Community replacement: returns an empty recordset.  The deferred report
        sub-handlers may extend this behaviour.
        """
        return self.env['account.move']


class AccountDeferredExpenseReportHandler(models.AbstractModel):
    _name = 'account.deferred.expense.report.handler'
    _inherit = ['account.deferred.report.handler']
    _description = 'msolutions Deferred Expense Report Handler'
