# -*- coding: utf-8 -*-
"""Confirming a sale order must NOT execute (validate) its delivery."""

from odoo.tests.common import TransactionCase


class TestSaleOrderConfirmAutoValidate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.StockPicking = cls.env['stock.picking']
        cls.SaleOrder = cls.env['sale.order']

        # ── storable product ──
        cls.product = cls.env['product.product'].create({
            'name': 'Test Storable Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 100.0,
        })

        # ── put some stock in the warehouse ──
        stock_location = cls.env.ref('stock.stock_location_stock')
        cls.env['stock.quant']._update_available_quantity(
            cls.product, stock_location, 10.0,
        )

        # ── partner ──
        cls.partner = cls.env['res.partner'].create({'name': 'Test Customer'})

    def _create_so(self):
        """Create a draft sale order with one line."""
        so = self.SaleOrder.create({
            'partner_id': self.partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_uom_qty': 3.0,
                    'price_unit': 100.0,
                }),
            ],
        })
        return so

    def test_confirm_does_not_validate_picking(self):
        """Confirm creates the delivery but leaves it undone."""
        so = self._create_so()
        self.assertEqual(so.state, 'draft')
        so.action_confirm()
        self.assertEqual(so.state, 'sale')
        self.assertTrue(so.picking_ids, "No picking was created for the SO")
        for picking in so.picking_ids:
            self.assertNotEqual(
                picking.state, 'done',
                "Confirming the order must not validate the delivery")
            self.assertEqual(sum(picking.move_ids.mapped('quantity_done')
                                 if 'quantity_done' in picking.move_ids._fields else [0]), 0)

    def test_delivery_can_still_be_validated_manually(self):
        so = self._create_so()
        so.action_confirm()
        picking = so.picking_ids[0]
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
