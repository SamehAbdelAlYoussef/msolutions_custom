from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentControls(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        if not cls.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)]):
            cls.env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
        cls.partner = cls.env['res.partner'].create({'name': 'Payer'})
        product = cls.env['product.product'].create({'name': 'P', 'list_price': 100.0})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 10, 'price_unit': 100.0})],
        })                                         # total 1000 (no taxes on the product)
        cls.total = cls.order.amount_total

    def _pay(self, amount, order=None, receipt=None, user=None):
        Payment = self.env['account.payment'].with_user(user) if user else self.env['account.payment']
        vals = {
            'partner_id': self.partner.id, 'payment_type': 'inbound', 'partner_type': 'customer',
            'amount': amount, 'journal_id': self.journal.id,
        }
        if order is not False:
            vals['sale_order_id'] = (order or self.order).id
        if receipt:
            vals['receipt_number'] = receipt
        return Payment.create(vals)

    def _user_in_group(self, in_group):
        user = self.env['res.users'].create({
            'name': 'Cashier %s' % in_group, 'login': 'cashier_%s@test.com' % in_group})
        sale = self.env.ref('sales_team.group_sale_salesman')
        acc = self.env.ref('account.group_account_invoice')
        ids = [sale.id, acc.id]
        if in_group:
            ids.append(self.env.ref('sale_payment_controls.group_allow_low_down_payment').id)
        user.write({'group_ids': [(4, i) for i in ids]})
        return user

    # -- 50% rule ----------------------------------------------------------

    def test_first_payment_below_half_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._pay(self.total * 0.49)

    def test_first_payment_of_exactly_half_is_accepted(self):
        self.assertTrue(self._pay(self.total * 0.5))

    def test_first_payment_above_half_is_accepted(self):
        self.assertTrue(self._pay(self.total * 0.8))

    def test_later_payment_can_be_small(self):
        self._pay(self.total * 0.5)
        self.assertTrue(self._pay(1.0))

    def test_cancelled_first_payment_does_not_count(self):
        first = self._pay(self.total * 0.5)
        first.state = 'canceled'
        with self.assertRaises(ValidationError):
            self._pay(10.0)           # no live earlier payment -> this is the "first"

    def test_allowed_user_can_register_a_low_first_payment(self):
        user = self._user_in_group(True)
        self.assertTrue(self._pay(10.0, user=user))

    def test_user_outside_the_group_is_blocked(self):
        user = self._user_in_group(False)
        with self.assertRaises(ValidationError):
            self._pay(10.0, user=user)

    def test_payment_without_an_order_is_not_affected(self):
        self.assertTrue(self._pay(5.0, order=False))

    def test_editing_the_amount_down_is_blocked(self):
        pay = self._pay(self.total * 0.6)
        with self.assertRaises(ValidationError):
            pay.amount = 5.0

    def test_minimum_percent_is_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sale_payment_controls.min_down_payment_percent', '20')
        self.assertTrue(self._pay(self.total * 0.25))

    # -- receipt / batch number -------------------------------------------

    def test_cannot_confirm_a_so_payment_without_receipt_number(self):
        pay = self._pay(self.total * 0.5)
        with self.assertRaises(UserError):
            pay.action_post()
        self.assertEqual(pay.state, 'draft')

    def test_blank_receipt_number_is_refused(self):
        pay = self._pay(self.total * 0.5, receipt='   ')
        with self.assertRaises(UserError):
            pay.action_post()

    def test_payment_confirms_once_the_receipt_number_is_registered(self):
        pay = self._pay(self.total * 0.5, receipt='B-1001')
        pay.action_post()
        self.assertNotEqual(pay.state, 'draft')
        self.assertEqual(pay.receipt_number, 'B-1001')

    def test_payment_without_an_order_confirms_without_a_number(self):
        pay = self._pay(5.0, order=False)
        pay.action_post()
        self.assertNotEqual(pay.state, 'draft')
