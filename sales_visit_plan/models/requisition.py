from markupsafe import Markup, escape

from odoo import models, fields, api, _
from odoo.exceptions import UserError

BRAND_COLOR = '#087F5B'


class SalesRequisition(models.Model):
    """A special request raised by a field sales rep (from the Sales Visit
    Plans app) — e.g. gifts for a doctor or authorization to dispense
    products. It flows through a sequential, per-step approval chain."""

    _name = 'sales.requisition'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Sales Requisition'
    _order = 'id desc'

    # ------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------
    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True,
    )
    user_id = fields.Many2one(
        'res.users', string='Requester',
        default=lambda self: self.env.user, required=True, tracking=True,
    )
    plan_id = fields.Many2one(
        'sales.plan', string='Related Visit Plan', tracking=True,
        help='Optional: the visit plan this requisition relates to.',
    )
    request_type = fields.Selection(
        selection=[
            ('gift', 'Doctor Gifts'),
            ('product', 'Product Dispense Authorization'),
        ],
        string='Request Type', default='gift', required=True, tracking=True,
    )
    doctor_name = fields.Char(string='Doctor / Beneficiary', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True)
    description = fields.Text(string='Justification')
    date_request = fields.Date(
        string='Request Date', default=fields.Date.context_today, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True,
    )
    line_ids = fields.One2many(
        'sales.requisition.line', 'requisition_id',
        string='Requested Products', copy=True,
    )
    approval_line_ids = fields.One2many(
        'sales.requisition.approval', 'requisition_id',
        string='Approval Chain', copy=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('to_approve', 'Under Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string='Status', default='draft', tracking=True,
        group_expand='_expand_states',
    )
    current_approval_id = fields.Many2one(
        'sales.requisition.approval', string='Current Step',
        compute='_compute_current_approval', store=True,
    )
    current_approver_id = fields.Many2one(
        related='current_approval_id.approver_id',
        string='Current Approver', store=True,
    )
    can_approve = fields.Boolean(
        compute='_compute_can_approve',
        help='Technical: the current user may decide on the active step.',
    )
    product_qty_total = fields.Float(
        string='Total Qty', compute='_compute_product_qty_total', store=True,
    )

    # ------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------
    @api.depends('approval_line_ids.state')
    def _compute_current_approval(self):
        for req in self:
            req.current_approval_id = req.approval_line_ids.filtered(
                lambda a: a.state == 'to_approve'
            )[:1]

    @api.depends_context('uid')
    @api.depends('current_approval_id', 'current_approval_id.approver_id', 'state')
    def _compute_can_approve(self):
        for req in self:
            req.can_approve = bool(
                req.state == 'to_approve'
                and self.env.user == req.current_approval_id.approver_id
            )

    @api.depends('line_ids.quantity')
    def _compute_product_qty_total(self):
        for req in self:
            req.product_qty_total = sum(req.line_ids.mapped('quantity'))

    def _expand_states(self, states, domain):
        return [key for key, _label in self._fields['state'].selection]

    # ------------------------------------------------------------
    # Create — assign sequence + default approval chain
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'sales.requisition'
                ) or _('New')
            if not vals.get('approval_line_ids'):
                vals['approval_line_ids'] = self._default_chain_commands()
        return super().create(vals_list)

    @api.model
    def _default_chain_commands(self):
        """Build (0,0,{...}) commands from the active global stages."""
        stages = self.env['sales.requisition.stage'].search(
            [('active', '=', True)], order='sequence, id',
        )
        return [(0, 0, {
            'sequence': stage.sequence,
            'name': stage.name,
            'approver_id': stage.approver_id.id,
            'stage_id': stage.id,
        }) for stage in stages]

    def action_reset_chain(self):
        """Re-seed the approval chain from the global stage templates."""
        for req in self:
            if req.state != 'draft':
                raise UserError(_(
                    'You can only reset the chain while the requisition '
                    'is in Draft.'
                ))
            req.approval_line_ids.unlink()
            req.approval_line_ids = req._default_chain_commands()

    # ------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------
    def action_submit(self):
        for req in self:
            if req.state != 'draft':
                raise UserError(_('Only draft requisitions can be submitted.'))
            if not req.line_ids:
                raise UserError(_(
                    'Add at least one product line before submitting.'
                ))
            if not req.approval_line_ids:
                raise UserError(_(
                    'This requisition has no approval chain. Configure '
                    'approval stages first, or reset the chain.'
                ))
            if any(not step.approver_id for step in req.approval_line_ids):
                raise UserError(_(
                    'Every approval step must have an approver assigned.'
                ))
            req.approval_line_ids.write({
                'state': 'pending',
                'approved_by_id': False,
                'approved_date': False,
            })
            first = req.approval_line_ids.sorted(
                lambda a: (a.sequence, a.id)
            )[:1]
            first.state = 'to_approve'
            req.state = 'to_approve'
            req.message_post(body=_(
                'Requisition submitted for approval by %s.'
            ) % self.env.user.name)
            req._notify_step_approver(first)

    def action_reset_to_draft(self):
        for req in self:
            if req.state not in ('rejected', 'to_approve'):
                raise UserError(_(
                    'Only in-progress or rejected requisitions can be '
                    'reset to draft.'
                ))
            req.approval_line_ids.write({
                'state': 'pending',
                'approved_by_id': False,
                'approved_date': False,
            })
            req.state = 'draft'
            req.message_post(body=_(
                'Requisition reset to draft by %s.'
            ) % self.env.user.name)

    def action_approve(self):
        """Header-button convenience: act on the active step."""
        self.ensure_one()
        if not self.current_approval_id:
            raise UserError(_('There is no step awaiting approval.'))
        self.current_approval_id.action_approve()

    def action_reject(self):
        self.ensure_one()
        if not self.current_approval_id:
            raise UserError(_('There is no step awaiting approval.'))
        self.current_approval_id.action_reject()

    # ---- called by the approval step (already under sudo) ----
    def _on_step_approved(self, step):
        self.ensure_one()
        remaining = self.approval_line_ids.filtered(
            lambda a: a.state == 'pending'
        ).sorted(lambda a: (a.sequence, a.id))
        if remaining:
            nxt = remaining[:1]
            nxt.state = 'to_approve'
            self._notify_step_approver(nxt)
        else:
            self.state = 'approved'
            self._notify_final_approved()

    def _on_step_rejected(self, step):
        self.ensure_one()
        self.state = 'rejected'
        self._notify_rejected(step)

    # ------------------------------------------------------------
    # Notifications — internal Discuss (OdooBot) + email
    # ------------------------------------------------------------
    def _notify_step_approver(self, step):
        self.ensure_one()
        if not step.approver_id:
            return
        subject = _('Approval needed: %s') % self.name
        body = self._render_notification(
            title=_('Requisition Awaiting Your Approval'),
            greeting=_('Dear %s,') % (step.approver_id.name or ''),
            intro=_('The following requisition needs your approval at '
                    'step "%s".') % step.name,
        )
        self._notify_user(step.approver_id, subject, body)
        self.message_post(body=_(
            'Step "%(step)s" is now awaiting approval from %(user)s.',
            step=step.name, user=step.approver_id.name,
        ))

    def _notify_final_approved(self):
        self.ensure_one()
        subject = _('Requisition approved: %s') % self.name
        body = self._render_notification(
            title=_('Requisition Fully Approved'),
            greeting=_('Dear %s,') % (self.user_id.name or ''),
            intro=_('Your requisition has passed all approval steps and '
                    'is now fully approved.'),
        )
        self._notify_user(self.user_id, subject, body)
        self.message_post(body=_('Requisition fully approved.'))

    def _notify_rejected(self, step):
        self.ensure_one()
        subject = _('Requisition rejected: %s') % self.name
        body = self._render_notification(
            title=_('Requisition Rejected'),
            greeting=_('Dear %s,') % (self.user_id.name or ''),
            intro=_('Your requisition was rejected at step "%(step)s" '
                    'by %(user)s.') % {
                        'step': step.name, 'user': self.env.user.name},
        )
        self._notify_user(self.user_id, subject, body)
        self.message_post(body=_(
            'Requisition rejected at step "%s".'
        ) % step.name)

    def _render_notification(self, title, greeting, intro):
        """Build a small branded HTML body reused for both the internal
        chat message and the email. All record-sourced values are escaped."""
        self.ensure_one()
        rows = ''.join(
            '<li>%s — <b>%s</b> %s</li>' % (
                escape(line.product_id.display_name),
                escape('%g' % line.quantity),
                escape(line.uom_id.name or ''),
            )
            for line in self.line_ids
        ) or ('<li>%s</li>' % escape(_('None')))
        type_label = dict(self._fields['request_type'].selection).get(
            self.request_type, '')
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;">'
            '<h2 style="color:%(c)s;">%(title)s</h2>'
            '<p>%(greeting)s</p>'
            '<p>%(intro)s</p>'
            '<table style="border-collapse:collapse;margin:12px 0;">'
            '<tr><td style="padding:4px 8px;"><b>%(ref_l)s</b></td>'
            '<td style="padding:4px 8px;">%(ref)s</td></tr>'
            '<tr><td style="padding:4px 8px;"><b>%(req_l)s</b></td>'
            '<td style="padding:4px 8px;">%(req)s</td></tr>'
            '<tr><td style="padding:4px 8px;"><b>%(doc_l)s</b></td>'
            '<td style="padding:4px 8px;">%(doc)s</td></tr>'
            '<tr><td style="padding:4px 8px;"><b>%(type_l)s</b></td>'
            '<td style="padding:4px 8px;">%(type)s</td></tr>'
            '</table>'
            '<p><b>%(prod_l)s</b></p><ul>%(rows)s</ul>'
            '<p style="color:#888;font-size:12px;margin-top:20px;">%(foot)s</p>'
            '</div>'
        ) % {
            'c': BRAND_COLOR,
            'title': escape(title),
            'greeting': escape(greeting),
            'intro': escape(intro),
            'ref_l': escape(_('Reference')), 'ref': escape(self.name or ''),
            'req_l': escape(_('Requester')), 'req': escape(self.user_id.name or ''),
            'doc_l': escape(_('Doctor / Beneficiary')),
            'doc': escape(self.doctor_name or '-'),
            'type_l': escape(_('Type')), 'type': escape(type_label),
            'prod_l': escape(_('Requested Products')),
            'rows': rows,
            'foot': escape(_('Automated message from the Sales Visit Plan system.')),
        }

    def _notify_user(self, user, subject, body_html):
        """Notify *user* three ways: an internal Discuss chat from OdooBot,
        an email, and a documented copy logged in the Related Visit Plan's
        chatter (so the whole approval trail is archived on the plan)."""
        self.ensure_one()
        if not user:
            return
        body = Markup(body_html)
        # 1) Internal chat — always lands in the user's Discuss inbox.
        odoobot = self.env.ref('base.user_root', raise_if_not_found=False)
        if user.partner_id and odoobot:
            channel = self.env['discuss.channel'].with_user(
                odoobot
            )._get_or_create_chat(partners_to=[user.partner_id.id])
            channel.with_user(odoobot).message_post(
                body=body,
                subject=subject,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        # 2) Email — guaranteed copy in the user's mailbox.
        if user.email:
            self.env['mail.mail'].sudo().create({
                'subject': subject,
                'body_html': body_html,
                'email_to': user.email,
                'auto_delete': True,
            }).send()
        # 3) Documentation — log a copy of the message on the Related
        #    Visit Plan's chatter, for every recipient.
        self._log_notification_on_plan(user, subject, body)

    def _log_notification_on_plan(self, user, subject, body):
        """Archive a copy of a notification on the Related Visit Plan's
        chatter so managers can see the full email/OdooBot trail there."""
        self.ensure_one()
        if not self.plan_id:
            return
        header = Markup(
            '<p style="margin:0 0 8px;color:%s;">'
            '<i class="fa fa-envelope" role="img" aria-label="Email"></i> '
            '<b>%%s</b><br/>'
            '<span style="color:#666;">%%s: %%s &lt;%%s&gt;</span></p>'
            % BRAND_COLOR
        ) % (
            subject,
            _('Sent (email + OdooBot chat) to'),
            user.name or '',
            user.email or _('no email'),
        )
        self.plan_id.sudo().message_post(
            body=header + body,
            subject=_('[Requisition %(ref)s] %(subj)s',
                      ref=self.name, subj=subject),
        )


class SalesRequisitionLine(models.Model):
    _name = 'sales.requisition.line'
    _description = 'Sales Requisition Product Line'

    requisition_id = fields.Many2one(
        'sales.requisition', string='Requisition',
        required=True, ondelete='cascade', index=True,
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
    )
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    uom_id = fields.Many2one(
        related='product_id.uom_id', string='Unit', readonly=True,
    )
    note = fields.Char(string='Note')
