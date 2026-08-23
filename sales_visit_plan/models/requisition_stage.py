from odoo import models, fields


class SalesRequisitionStage(models.Model):
    """Global, admin-configured approval stage (a 'department' + its
    designated approver). The active stages are copied — in sequence
    order — onto every new requisition as its default approval chain."""

    _name = 'sales.requisition.stage'
    _description = 'Sales Requisition Approval Stage (Template)'
    _order = 'sequence, id'

    sequence = fields.Integer(string='Order', default=10)
    name = fields.Char(string='Department / Step', required=True, translate=True)
    approver_id = fields.Many2one(
        'res.users',
        string='Default Approver',
        required=True,
        help='The user allowed to approve this step. Can be overridden per '
             'requisition while it is still a draft.',
    )
    active = fields.Boolean(default=True)
