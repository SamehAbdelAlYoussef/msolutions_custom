from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SalesRequisitionApproval(models.Model):
    """One step in a requisition's sequential approval chain. Exactly one
    step is 'to_approve' at a time; its single approver decides, then the
    next step in sequence becomes active."""

    _name = 'sales.requisition.approval'
    _description = 'Sales Requisition Approval Step'
    _order = 'sequence, id'

    requisition_id = fields.Many2one(
        'sales.requisition', string='Requisition',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(string='Order', default=10)
    name = fields.Char(string='Step / Department', required=True)
    stage_id = fields.Many2one(
        'sales.requisition.stage', string='Stage Template',
        ondelete='set null',
    )
    approver_id = fields.Many2one(
        'res.users', string='Approver', required=True,
        help='The only user (besides a Sales Manager) allowed to decide '
             'on this step.',
    )
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('to_approve', 'To Approve'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string='Status', default='pending', required=True, copy=False,
    )
    approved_by_id = fields.Many2one(
        'res.users', string='Decided By', readonly=True, copy=False,
    )
    approved_date = fields.Datetime(
        string='Decision Date', readonly=True, copy=False,
    )
    note = fields.Text(string='Decision Note')
    requester_id = fields.Many2one(
        related='requisition_id.user_id', string='Requester', store=True,
    )

    # ------------------------------------------------------------
    # Decision guard
    # ------------------------------------------------------------
    def _check_can_decide(self):
        """ONLY the step's assigned approver may act, and only while the
        step is the active one. There is no manager override — a user can
        decide a step only if they are the one assigned to it in the chain."""
        self.ensure_one()
        if self.state != 'to_approve':
            raise UserError(_(
                'The step "%s" is not currently awaiting a decision.'
            ) % self.name)
        if self.env.user != self.approver_id:
            raise UserError(_(
                'Only %(approver)s can decide on the step "%(step)s". '
                'You are not the assigned approver for this step.',
                approver=self.approver_id.name, step=self.name,
            ))

    def action_approve(self):
        self.ensure_one()
        self._check_can_decide()
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approved_date': fields.Datetime.now(),
        })
        self.requisition_id.message_post(body=_(
            'Step "%(step)s" approved by %(user)s.',
            step=self.name, user=self.env.user.name,
        ))
        # Advancing the chain writes to the *next* step (another user's
        # record) and to the requisition — orchestrate with sudo. The
        # decision guard above already enforced who may act.
        self.requisition_id.sudo()._on_step_approved(self.sudo())

    def action_reject(self):
        self.ensure_one()
        self._check_can_decide()
        self.write({
            'state': 'rejected',
            'approved_by_id': self.env.user.id,
            'approved_date': fields.Datetime.now(),
        })
        self.requisition_id.message_post(body=_(
            'Step "%(step)s" rejected by %(user)s.',
            step=self.name, user=self.env.user.name,
        ))
        self.requisition_id.sudo()._on_step_rejected(self.sudo())
