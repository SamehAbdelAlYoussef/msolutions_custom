# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    scale_id = fields.Many2one('scale.scale', string='Scale')
    scale_discount = fields.Float(string='Scale Discount %', digits='Discount')
    qty_after_scale_discount = fields.Float(
        string='Qty After Scale Discount',
        compute='_compute_qty_after_scale_discount',
        store=True,
        digits='Product Unit of Measure')
    # Vendor from whom the product was purchased (from PO)
    source_vendor_id = fields.Many2one(
        'res.partner', string='Vendor', copy=False)
    total_purchase_customer = fields.Monetary(
        string='Total Purchase Customer',
        currency_field='currency_id',
        default=0.0)

    @api.depends('total_purchase_customer')
    def _compute_amount(self):
        """Trigger recompute when total_purchase_customer changes."""
        super()._compute_amount()

    def _prepare_base_line_for_taxes_computation(self):
        """Use total_purchase_customer as the line total when set."""
        vals = super()._prepare_base_line_for_taxes_computation()
        if self.total_purchase_customer:
            vals['price_unit'] = self.total_purchase_customer
            vals['quantity'] = 1.0
            vals['discount'] = 0.0
        return vals

    @api.depends('product_uom_qty', 'scale_discount')
    def _compute_qty_after_scale_discount(self):
        """Qty after discount = qty * (1 - scale_discount %)"""
        for line in self:
            line.qty_after_scale_discount = line.product_uom_qty * (
                1 - (line.scale_discount or 0.0) / 100.0)

    @api.onchange('scale_id')
    def _onchange_scale_id(self):
        for line in self:
            line.scale_discount = line.scale_id.discount if line.scale_id else 0.0

    @api.depends('scale_id', 'scale_discount')
    def _compute_discount(self):
        """Scale discount is applied as actual line discount (overrides pricelist)."""
        scale_lines = self.filtered('scale_id')
        # Pricelist computation only for non-scale lines (save work)
        super(SaleOrderLine, self - scale_lines)._compute_discount()
        for line in scale_lines:
            line.discount = line.scale_discount

    def _compute_price_unit(self):
        """Lines from purchase orders or with total_purchase_customer:
        skip pricelist price computation (set manually, save on qty change)."""
        skip_lines = self.filtered(
            lambda l: l.source_vendor_id or l.total_purchase_customer)
        # Re-assigning the same value = signal to ORM that field was computed
        for line in skip_lines:
            line.price_unit = line.price_unit
        super(SaleOrderLine, self - skip_lines)._compute_price_unit()
