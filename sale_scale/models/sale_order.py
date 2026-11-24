# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _action_confirm(self):
        res = super()._action_confirm()
        # Auto-validate delivery pickings after order confirmation
        self._auto_validate_pickings()
        # Then create and post the customer invoice
        self._auto_create_invoices()
        return res

    def _auto_create_invoices(self):
        """Create and post the customer invoice of each confirmed order that
        has something to invoice and no invoice yet."""
        self.env.flush_all()
        for order in self:
            if order.invoice_status != 'to invoice' or order.invoice_ids:
                continue
            order._create_invoices().action_post()

    def _auto_validate_pickings(self):
        """Auto-validate all delivery orders for the sale order."""
        self.env.flush_all()
        for order in self:
            # Odoo 19 has no procurement group on the sale order: the delivery
            # orders are linked to it through stock.picking.sale_id
            pickings = self.env['stock.picking'].search([
                ('sale_id', '=', order.id),
                ('state', 'not in', ('done', 'cancel')),
            ])
            _logger.info(
                "Auto-validating %s pickings for %s: %s",
                len(pickings), order.name,
                [(p.name, p.state) for p in pickings],
            )
            for picking in pickings:
                if picking.state in ('done', 'cancel'):
                    continue
                # Set all move done quantities = demand quantities
                for move in picking.move_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                ):
                    move.quantity = move.product_uom_qty
                # Validate directly (skip backorder wizard)
                try:
                    picking.with_context(
                        skip_backorder=True,
                        skip_sanity_check=True,
                    ).button_validate()
                    _logger.info("Picking %s validated", picking.name)
                except Exception as e:
                    _logger.warning(
                        "Auto-validate picking %s failed: %s", picking.name, e
                    )
