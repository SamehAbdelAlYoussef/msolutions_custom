import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

# Pre-compiled patterns for _parse_google_maps_url
_GMAPS_RE_AT = re.compile(r'/@(-?\d+\.\d+),(-?\d+\.\d+)')
_GMAPS_RE_Q = re.compile(r'[?&]q=(-?\d+\.\d+)\s*,?\s*(-?\d+\.\d+)')
_GMAPS_RE_PLACE = re.compile(r'/(-?\d+\.\d+),(-?\d+\.\d+)(?:/|[?&]|$)')
_GMAPS_RE_LL = re.compile(r'll=(-?\d+\.\d+),(-?\d+\.\d+)')


class SalesPlan(models.Model):
    _name = 'sales.plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Sales Visit Plan'
    _order = 'date_start desc, id desc'

    # ------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------
    name = fields.Char(
        string='Plan Name',
        required=True,
        tracking=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        default=lambda self: self.env.user,
        required=True,
        tracking=True,
    )
    manager_id = fields.Many2one(
        'res.users',
        string='Approval Manager',
        tracking=True,
        help='The manager who will approve this plan and receive the approval request email.',
    )
    region = fields.Char(
        string='Region / Governorate',
        tracking=True,
    )
    date_start = fields.Date(
        string='Start Date',
        tracking=True,
    )
    date_end = fields.Date(
        string='End Date',
        tracking=True,
    )
    duration_days = fields.Integer(
        string='Duration (Days)',
        compute='_compute_duration_days',
        store=True,
    )
    state = fields.Selection(
        selection=[
            ('new', 'New'),
            ('to_approve', 'Waiting for Approval'),
            ('approved', 'Approved'),
        ],
        string='Status',
        default='new',
        tracking=True,
        group_expand='_expand_states',
    )
    line_ids = fields.One2many(
        'sales.plan.line',
        'plan_id',
        string='Visit Lines',
        copy=True,
    )
    manager_notes = fields.Text(
        string='Manager Notes',
        translate=True,
    )
    visit_count = fields.Integer(
        string='Visit Count',
        compute='_compute_visit_count',
        store=True,
    )
    location_url = fields.Char(
        string='Location URL',
        tracking=True,
        help='Paste a Google Maps / Waze link or use '
             '"📍 Get My Location" to capture GPS coordinates.',
    )
    partner_latitude = fields.Float(
        string='Geo Latitude',
        digits=(10, 7),
        help='Plan-level coordinates, used by the map view.',
    )
    partner_longitude = fields.Float(
        string='Geo Longitude',
        digits=(10, 7),
        help='Plan-level coordinates, used by the map view.',
    )
    show_lock_overlay = fields.Boolean(
        compute='_compute_show_lock_overlay',
        help='Technical field: shows lock overlay on kanban cards for '
             'non-managers when the plan is pending approval.',
    )

    # ------------------------------------------------------------
    # Compute Methods
    # ------------------------------------------------------------
    @api.depends('date_start', 'date_end')
    def _compute_duration_days(self):
        for plan in self:
            if plan.date_start and plan.date_end:
                delta = plan.date_end - plan.date_start
                plan.duration_days = delta.days if delta.days >= 0 else 0
            else:
                plan.duration_days = 0

    @api.depends('line_ids')
    def _compute_visit_count(self):
        if self.ids:
            groups = self.env['sales.plan.line'].read_group(
                [('plan_id', 'in', self.ids)],
                ['plan_id'], ['plan_id'],
            )
            count_map = {g['plan_id'][0]: g['plan_id_count'] for g in groups}
            for plan in self:
                plan.visit_count = count_map.get(plan.id, 0)
        else:
            for plan in self:
                plan.visit_count = 0

    @api.depends('state')
    def _compute_show_lock_overlay(self):
        """Show a lock overlay on kanban cards for non-managers when
        the plan is waiting for approval."""
        is_manager = self.env.user.has_group(
            'sales_visit_plan.group_sales_manager'
        )
        for plan in self:
            plan.show_lock_overlay = (
                plan.state == 'to_approve' and not is_manager
            )

    # ------------------------------------------------------------
    # Override write() — block non-managers from approving plans
    # ------------------------------------------------------------
    def write(self, vals):
        if vals.get('state') == 'approved':
            if not self.env.user.has_group(
                'sales_visit_plan.group_sales_manager'
            ):
                raise UserError(
                    _(
                        'Only Sales Managers can approve plans.\n'
                        'Plan: %(plan_names)s\n'
                        'Please contact your manager to approve this plan.',
                        plan_names=', '.join(self.mapped('name')),
                    )
                )
        return super().write(vals)

    # ------------------------------------------------------------
    # Helper: keep empty groups visible in Kanban
    # ------------------------------------------------------------
    def _expand_states(self, states, domain):
        return [key for key, _ in self._fields['state'].selection]

    # ------------------------------------------------------------
    # Action: Open Visit Lines Kanban (full screen)
    # ------------------------------------------------------------
    def action_open_visit_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': '%s - Visit Lines' % self.name,
            'res_model': 'sales.plan.line',
            'view_mode': 'kanban,list,form,map',
            'domain': [('plan_id', '=', self.id)],
            'context': {
                'default_plan_id': self.id,
                'search_default_group_by_visit_stage': 1,
                'kanban_view_ref': 'sales_visit_plan.sales_plan_line_kanban',
            },
        }

    # ------------------------------------------------------------
    # Action: Request Approval
    # ------------------------------------------------------------
    def action_request_approval(self):
        """Move plan from 'new' to 'to_approve', notify managers."""
        self.ensure_one()
        if self.state != 'new':
            raise UserError(
                _('Only plans in "New" status can request approval.')
            )

        self.state = 'to_approve'

        managers = self._get_sales_managers()

        # Send email to all sales managers
        template = self.env.ref(
            'sales_visit_plan.email_template_approval_request',
            raise_if_not_found=False,
        )
        if template:
            for manager in managers:
                template.send_mail(
                    self.id,
                    email_values={'email_to': manager.email},
                )

        # Create a To-Do activity for each manager
        self._create_manager_activity(managers)

        # Post a note in the chatter
        self.message_post(
            body=_('Plan sent for approval by %s.') % self.env.user.name
        )

    # ------------------------------------------------------------
    # Action: Approve Plan  (manager only)
    # ------------------------------------------------------------
    def action_approve_plan(self):
        """Manager approves the plan — moves to 'approved'."""
        self.ensure_one()
        if self.state != 'to_approve':
            raise UserError(
                _('Only plans in "Waiting for Approval" status can be approved.')
            )
        if not self.env.user.has_group('sales_visit_plan.group_sales_manager'):
            raise UserError(
                _('Only Sales Managers can approve plans.')
            )

        self.state = 'approved'

        # Send confirmation to the salesperson
        template = self.env.ref(
            'sales_visit_plan.email_template_approval_confirmation',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id)

        # Schedule activity for the salesperson
        self._create_salesperson_activity()

        # Post a note in the chatter
        self.message_post(
            body=_('Plan approved by %s.') % self.env.user.name
        )

    # ------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------
    def _get_sales_managers(self):
        """Return the manager(s) to notify for plan approval."""
        self.ensure_one()
        if self.manager_id:
            return self.manager_id
        # Fallback: users in group_sales_manager
        group = self.env.ref(
            'sales_visit_plan.group_sales_manager', raise_if_not_found=False
        )
        users = group.user_ids if group else self.env['res.users']
        return users or self.env.ref('base.user_admin', raise_if_not_found=False)

    def _create_salesperson_activity(self):
        """Schedule an activity for the salesperson when plan is approved."""
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False
        )
        if not activity_type or not self.user_id:
            return
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.user_id.id,
            summary=_('Plan Approved: %s') % self.name,
            note=_('Your visit plan "%s" has been approved. You can now start your visits.', self.name),
        )

    def _create_manager_activity(self, managers):
        """Schedule a To-Do activity on the current plan for each manager."""
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False
        )
        if not activity_type:
            return

        for manager in managers:
            self.activity_schedule(
                activity_type_id=activity_type.id,
                user_id=manager.id,
                summary=_('Approve Sales Plan: %s') % self.name,
                note=_(
                    'Please review and approve the sales visit plan "%(plan)s" '
                    'created by %(user)s.',
                    plan=self.name,
                    user=self.user_id.name,
                ),
            )


class SalesPlanLine(models.Model):
    _name = 'sales.plan.line'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Sales Visit Plan Line'
    _order = 'visit_date, id'

    # ------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------
    plan_id = fields.Many2one(
        'sales.plan',
        string='Plan',
        required=True,
        ondelete='cascade',
        index=True,
    )
    doctor_name = fields.Char(
        string='Doctor / Pharmacist Name',
        required=True,
        tracking=True,
    )
    phone = fields.Char(
        string='Phone',
        tracking=True,
    )
    specialty = fields.Char(
        string='Specialty',
        tracking=True,
    )
    pharmacy_name = fields.Char(
        string='Pharmacy Name',
        tracking=True,
    )
    visit_stage = fields.Selection(
        selection=[
            ('first_visit', 'First Visit'),
            ('repeat_visit', 'Repeat Visit'),
            ('completed', 'Completed'),
        ],
        string='Visit Stage',
        default='first_visit',
        tracking=True,
        group_expand='_expand_visit_stages',
    )
    gift_ids = fields.Many2many(
        'product.product',
        string='Gifts',
        tracking=True,
    )
    gift_notes = fields.Text(
        string='Gift Notes',
        tracking=True,
        translate=True,
    )
    visit_date = fields.Date(
        string='Scheduled Visit Date',
        tracking=True,
    )
    location_url = fields.Char(
        string='Location URL',
        tracking=True,
        help='Paste a Google Maps / Waze link or any location URL '
             'so you can open the visit location directly.',
    )
    partner_latitude = fields.Float(
        string='Geo Latitude',
        digits=(10, 7),
        help='Auto-parsed from the Google Maps URL. Used by the map '
             'view to display the pin — same field name as res.partner.',
    )
    partner_longitude = fields.Float(
        string='Geo Longitude',
        digits=(10, 7),
        help='Auto-parsed from the Google Maps URL. Used by the map '
             'view to display the pin — same field name as res.partner.',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Contact',
        tracking=True,
    )
    lead_id = fields.Many2one(
        'crm.lead',
        string='Opportunity',
        tracking=True,
    )

    # ------------------------------------------------------------
    # Helper: keep all 3 kanban columns visible
    # ------------------------------------------------------------
    def _expand_visit_stages(self, states, domain):
        return [key for key, _ in self._fields['visit_stage'].selection]

    # ------------------------------------------------------------
    # Location URL → Coordinates parsing
    # ------------------------------------------------------------
    @api.onchange('location_url')
    def _onchange_location_url(self):
        """Parse a Google Maps URL and auto-fill partner_latitude / partner_longitude.
        Clear coordinates when the URL is removed."""
        if self.location_url:
            lat, lng = self._parse_google_maps_url(self.location_url)
            if lat is not None:
                self.partner_latitude = lat
                self.partner_longitude = lng
        else:
            self.partner_latitude = False
            self.partner_longitude = False

    def _parse_google_maps_url(self, url):
        """Extract (lat, lng) from common Google Maps URL formats.
        Returns (None, None) when the URL cannot be parsed.

        NOTE: Only standard Google Maps share URLs are supported.
        Short links (goo.gl, maps.app.goo.gl), Waze URLs, and the
        newer ``data=`` format require HTTP redirects or bespoke
        decoding and are intentionally not handled here.
        """
        if not url:
            return None, None
        for pattern in (
            _GMAPS_RE_AT,    # .../@LAT,LNG,ZOOM  (most common)
            _GMAPS_RE_Q,     # ...?q=LAT,LNG
            _GMAPS_RE_PLACE, # .../place/LAT,LNG
            _GMAPS_RE_LL,    # ...ll=LAT,LNG  (older style)
        ):
            m = pattern.search(url)
            if m:
                return float(m.group(1)), float(m.group(2))
        return None, None

    # ------------------------------------------------------------
    # Auto-create / update partner with coordinates for the map
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_location_url_to_vals(vals)
        records = super().create(vals_list)
        records._sync_coords_to_partner()
        return records

    def write(self, vals):
        coords_updated = self._apply_location_url_to_vals(vals)
        res = super().write(vals)
        if coords_updated:
            self._sync_coords_to_partner()
        return res

    def _apply_location_url_to_vals(self, vals):
        """If *vals* contains a location_url but no partner_latitude,
        parse the URL and inject lat/lng.  Returns True when coords were added."""
        url = vals.get('location_url')
        if url and 'partner_latitude' not in vals:
            lat, lng = self._parse_google_maps_url(url)
            if lat is not None:
                vals['partner_latitude'] = lat
                vals['partner_longitude'] = lng
                return True
        return 'partner_latitude' in vals or 'partner_longitude' in vals

    def _sync_coords_to_partner(self):
        """Mirror the lineʼs coordinates onto its res.partner so both
        stay in sync and the map view can read either source."""
        for line in self:
            if not line.partner_latitude or not line.partner_longitude:
                continue
            if line.partner_id:
                line.partner_id.write({
                    'partner_latitude': line.partner_latitude,
                    'partner_longitude': line.partner_longitude,
                })
            else:
                partner = line._find_or_create_partner()
                partner.write({
                    'partner_latitude': line.partner_latitude,
                    'partner_longitude': line.partner_longitude,
                })
                line.partner_id = partner.id

    def _find_or_create_partner(self):
        """Return an existing res.partner matching this line's doctor
        name and phone, or create a new one.  The line's partner_id is
        NOT updated here — callers must assign it themselves."""
        self.ensure_one()
        partner = self.env['res.partner'].search([
            ('name', '=', self.doctor_name),
            ('phone', '=', self.phone),
        ], limit=1)
        if not partner:
            partner = self.env['res.partner'].create({
                'name': self.doctor_name,
                'phone': self.phone,
                'company_type': 'person',
            })
        return partner

    # ------------------------------------------------------------
    def action_mark_completed(self):
        self.ensure_one()
        self.visit_stage = 'completed'
        # Auto-create contact + CRM lead
        self._create_partner_and_lead()
        self.message_post(body=_('Visit marked as Completed.'))

    def _create_partner_and_lead(self):
        """Create a res.partner and crm.lead from visit data."""
        partner = self._find_or_create_partner()
        self.partner_id = partner.id

        # Create CRM lead
        lead = self.env['crm.lead'].create({
            'name': '%s - %s' % (self.doctor_name, self.specialty or 'Visit'),
            'partner_id': partner.id,
            'phone': self.phone,
            'type': 'opportunity',
            'description': '%s\nPharmacy: %s\nVisit Date: %s\nGifts: %s\nNotes: %s' % (
                self.doctor_name,
                self.pharmacy_name or 'N/A',
                self.visit_date or 'N/A',
                ', '.join(self.gift_ids.mapped('name')),
                self.gift_notes or 'N/A',
            ),
        })
        self.lead_id = lead.id

    def action_open_lead(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'res_id': self.lead_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.partner_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_mark_planned(self):
        self.ensure_one()
        self.visit_stage = 'first_visit'
        self.message_post(body=_('Visit reopened as Planned.'))

    def action_convert_to_repeat(self):
        """Change visit_stage from 'first_visit' to 'repeat_visit'."""
        self.ensure_one()
        if self.visit_stage != 'first_visit':
            raise UserError(
                _('Only "First Visit" lines can be converted to repeat visits.')
            )
        self.visit_stage = 'repeat_visit'
        self.message_post(
            body=_('Visit converted to Repeat Visit by %(user)s.', user=self.env.user.name)
        )
