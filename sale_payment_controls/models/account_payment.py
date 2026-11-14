# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

MIN_PERCENT_PARAM = 'sale_payment_controls.min_down_payment_percent'
LOW_PAYMENT_GROUP = 'sale_payment_controls.group_allow_low_down_payment'


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    receipt_number = fields.Char(
        string='Receipt / Batch No.', copy=False, index=True, tracking=True,
        help="Payment receipt number or card-machine batch number. Required "
             "to confirm a payment of a sales order.")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _is_so_advance(self):
        self.ensure_one()
        return bool(self.sale_order_id) and self.payment_type == 'inbound'

    def _min_down_payment_percent(self):
        value = self.env['ir.config_parameter'].sudo().get_param(MIN_PERCENT_PARAM, '50')
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 50.0

    def _check_first_payment_minimum(self):
        """The first advance payment of a sales order must cover the minimum %.

        "First" = no earlier, non-cancelled inbound payment on the same order.
        Later payments can be any amount. Users in LOW_PAYMENT_GROUP are exempt.
        """
        if self.env.user.has_group(LOW_PAYMENT_GROUP):
            return
        percent = self._min_down_payment_percent()
        if not percent:
            return
        for pay in self:
            if not pay._is_so_advance() or pay.state in ('canceled', 'rejected'):
                continue
            # sudo: the cashier may not be allowed to read this order, but the
            # rule must still be applied against its real total.
            order = pay.sale_order_id.sudo()
            earlier = self.sudo().search_count([
                ('sale_order_id', '=', order.id),
                ('payment_type', '=', 'inbound'),
                ('state', 'not in', ('canceled', 'rejected')),
                ('id', '<', pay.id),
            ])
            if earlier or not order.amount_total:
                continue
            paid = pay.currency_id._convert(
                pay.amount, order.currency_id, order.company_id,
                pay.date or fields.Date.context_today(pay))
            required = order.amount_total * percent / 100.0
            if order.currency_id.compare_amounts(paid, required) < 0:
                raise ValidationError(_(
                    "The first payment of %(order)s must be at least %(pct)g%% of the "
                    "order total (%(required)s). You entered %(paid)s.\n"
                    "Ask a user who is allowed to accept a lower first payment.",
                    order=order.name, pct=percent,
                    required=order.currency_id.format(required),
                    paid=order.currency_id.format(paid)))

    # ------------------------------------------------------------------
    # enforcement
    # ------------------------------------------------------------------
    def create(self, vals_list):
        payments = super().create(vals_list)
        payments._check_first_payment_minimum()
        return payments

    def write(self, vals):
        res = super().write(vals)
        if {'amount', 'sale_order_id', 'payment_type', 'currency_id', 'state'} & set(vals):
            self._check_first_payment_minimum()
        return res

    def action_post(self):
        for pay in self:
            if pay._is_so_advance() and not (pay.receipt_number or '').strip():
                raise UserError(_(
                    "Enter the receipt / batch number of this payment before "
                    "confirming it."))
        self._check_first_payment_minimum()
        return super().action_post()
