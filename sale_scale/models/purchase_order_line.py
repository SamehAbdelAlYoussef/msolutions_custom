# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    scale_id = fields.Many2one('scale.scale', string='Scale')
    scale_discount = fields.Float(string='Scale Discount %', digits='Discount')
    qty_after_scale_discount = fields.Float(
        string='Qty After Scale Discount',
        compute='_compute_qty_after_scale_discount',
        store=True,
        digits='Product Unit of Measure')
    # Customer for whom a sale order will be created after PO confirmation
    customer_id = fields.Many2one(
        'res.partner', string='Customer', copy=False)
    # Unit price the customer pays: becomes the unit price of the generated
    # sale order line. It has no effect on the amounts of the purchase order.
    total_purchase_customer = fields.Monetary(
        string='Sales Price',
        currency_field='currency_id',
        default=0.0)

    # The sale order line created from this PO line (to prevent duplicates)
    generated_sale_line_id = fields.Many2one(
        'sale.order.line', string='Generated Sale Line', readonly=True, copy=False)

    @api.depends('product_qty', 'scale_discount')
    def _compute_qty_after_scale_discount(self):
        """Qty after discount = qty * (1 - scale_discount %)"""
        for line in self:
            line.qty_after_scale_discount = line.product_qty * (
                1 - (line.scale_discount or 0.0) / 100.0)

    @api.onchange('scale_id')
    def _onchange_scale_id(self):
        for line in self:
            line.scale_discount = line.scale_id.discount if line.scale_id else 0.0

    # Fields synced from PO line to linked sale order line
    _SALE_SYNC_FIELDS = (
        'product_id', 'product_qty', 'product_uom_id', 'scale_id', 'scale_discount',
        'total_purchase_customer')
    # Fields with different names in sale.order.line (the rest use the same name)
    _SALE_FIELD_RENAMES = {
        'product_qty': 'product_uom_qty',
        'total_purchase_customer': 'price_unit',  # Sales Price -> selling unit price
    }

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # If a new line has a customer → create/update sale order immediately
        if any(v.get('customer_id') for v in vals_list):
            lines.order_id._create_customer_sale_orders()
        return lines

    def unlink(self):
        # Delete linked sale order lines before deleting PO lines
        sale_lines_to_delete = self.generated_sale_line_id.filtered(
            lambda l: l.order_id.state in ('draft', 'sent'))
        if sale_lines_to_delete:
            sale_lines_to_delete.sudo().unlink()
        return super().unlink()

    def write(self, vals):
        res = super().write(vals)
        # If customer was set on an existing line → create/update sale order
        if vals.get('customer_id'):
            self.order_id._create_customer_sale_orders()
        # Sync any changed fields to the linked sale order line
        sync_keys = set(vals) & set(self._SALE_SYNC_FIELDS)
        if sync_keys:
            for line in self:
                sale_line = line.generated_sale_line_id
                # Only sync while sale order is still draft/sent
                if not sale_line or sale_line.order_id.state not in ('draft', 'sent'):
                    continue
                sync_vals = {
                    self._SALE_FIELD_RENAMES.get(k, k): vals[k] for k in sync_keys}
                if 'product_id' in sync_keys:
                    sync_vals['name'] = "%s\nVendor: %s" % (
                        line.product_id.display_name,
                        line.order_id.partner_id.name)
                sale_line.sudo().write(sync_vals)
        return res

    @api.depends('scale_id', 'scale_discount')
    def _compute_price_unit_and_date_planned_and_name(self):
        """Scale discount is applied as actual line discount."""
        super()._compute_price_unit_and_date_planned_and_name()
        for line in self:
            if line.scale_id:
                line.discount = line.scale_discount
