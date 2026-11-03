# -*- coding: utf-8 -*-
from odoo import fields, models


class ScaleScale(models.Model):
    _name = 'scale.scale'
    _description = 'Scale'
    _order = 'name'

    name = fields.Char(string='Scale Name', required=True)
    discount = fields.Float(string='Scale Discount %', digits='Discount')
    active = fields.Boolean(default=True)
