import json
import re
import urllib.request

from odoo import models, fields, api, _
from odoo.exceptions import UserError

# Pre-compiled patterns for _parse_google_maps_url
_GMAPS_RE_AT = re.compile(r'/@(-?\d+\.\d+),(-?\d+\.\d+)')
_GMAPS_RE_Q = re.compile(r'[?&]q=(-?\d+\.\d+)\s*,?\s*(-?\d+\.\d+)')
_GMAPS_RE_PLACE = re.compile(r'/(-?\d+\.\d+),(-?\d+\.\d+)(?:/|[?&]|$)')
_GMAPS_RE_LL = re.compile(r'll=(-?\d+\.\d+),(-?\d+\.\d+)')

# Nominatim reverse geocoding URL (OpenStreetMap – free, no API key)
_NOMINATIM_URL = (
    'https://nominatim.openstreetmap.org/reverse'
    '?format=json&lat={lat}&lon={lon}&addressdetails=1&accept-language=ar'
)


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
        tracking=True,
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
    is_locked = fields.Boolean(
        compute='_compute_is_locked',
        help='True when the plan is waiting for approval and the '
             'current user is not a manager — used to make forms readonly.',
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

    @api.depends('state')
    def _compute_is_locked(self):
        """Lock the form for non-managers when the plan is pending approval."""
        is_manager = self.env.user.has_group(
            'sales_visit_plan.group_sales_manager'
        )
        for plan in self:
            plan.is_locked = (
                plan.state == 'to_approve' and not is_manager
            )

    # ------------------------------------------------------------
    # Override write() — enforce approval workflow
    # ------------------------------------------------------------
    def write(self, vals):
        is_manager = self.env.user.has_group(
            'sales_visit_plan.group_sales_manager'
        )
        # Only managers can approve
        if vals.get('state') == 'approved' and not is_manager:
            raise UserError(_(
                'Only Sales Managers can approve plans.\n'
                'Plan: %(plan_names)s\n'
                'Please contact your manager to approve this plan.',
                plan_names=', '.join(self.mapped('name')),
            ))
        # Non-managers cannot edit plans that are waiting for approval
        # (except moving them back to New)
        if not is_manager and set(vals.keys()) != {'state'} and vals.get('state') != 'new':
            for plan in self:
                if plan.state == 'to_approve':
                    raise UserError(_(
                        'The plan "%(plan)s" is waiting for approval.\n'
                        'You cannot modify it. Please move it back to '
                        '"New" first, make your changes, then resend '
                        'for approval.',
                        plan=plan.name,
                    ))
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
    _rec_name = 'doctor_name'
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
    plan_locked = fields.Boolean(
        related='plan_id.is_locked',
        string='Plan is Locked',
        help='True when the parent plan is waiting for approval '
             'and the user is not a manager.',
    )
    salesperson_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        related='plan_id.user_id',
        store=True,
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
    location_details = fields.Html(
        string='Location Details',
        help='Detailed address reverse-geocoded from the location URL '
             'coordinates. Updated automatically when the URL changes.',
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
        """Parse a Google Maps URL and auto-fill partner_latitude /
        partner_longitude and location_details.  Clears coordinates
        and details when the URL is removed."""
        if self.location_url:
            lat, lng = self._parse_google_maps_url(self.location_url)
            if lat is not None:
                self.partner_latitude = lat
                self.partner_longitude = lng
                # Reverse-geocode and append to existing details
                addr = self._reverse_geocode(lat, lng)
                if addr:
                    self.location_details = self._append_to_html_list(
                        self.location_details, addr
                    )
        else:
            self.partner_latitude = False
            self.partner_longitude = False
            self.location_details = False

    def _append_to_html_list(self, existing_html, new_item):
        """Append *new_item* as a <li> to an existing HTML string."""
        if not existing_html:
            return f'<ul><li>{new_item}</li></ul>'
        if '</ul>' in existing_html:
            # Append to existing <ul>
            return existing_html.replace(
                '</ul>', f'<li>{new_item}</li></ul>'
            )
        # Wrap existing content in a <ul> and add the new item
        return f'<ul><li>{existing_html}</li><li>{new_item}</li></ul>'

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

    def _reverse_geocode(self, lat, lng):
        """Convert (lat, lng) to a human-readable address using Nominatim.
        Returns a string like '١٢ شارع التحرير, القاهرة' or None on failure."""
        if lat is None or lng is None:
            return None
        try:
            url = _NOMINATIM_URL.format(lat=lat, lon=lng)
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Odoo-SalesVisitPlan/19.0'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data and data.get('display_name'):
                return data['display_name']
        except Exception:
            pass
        return None

    # ------------------------------------------------------------
    # Auto-create / update partner with coordinates for the map
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_location_url_to_vals(vals)
        records = super().create(vals_list)
        records._sync_coords_to_partner()
        # Schedule visit activity on the plan for lines that have a date
        records.filtered(lambda r: r.visit_date)._update_visit_activity()
        # Post creation message in chatter
        for rec in records:
            rec.message_post(
                body=_(
                    'Visit created for %(doctor)s\n'
                    'Phone: %(phone)s\n'
                    'Pharmacy: %(pharmacy)s\n'
                    'Date: %(date)s',
                    doctor=rec.doctor_name,
                    phone=rec.phone or 'N/A',
                    pharmacy=rec.pharmacy_name or 'N/A',
                    date=rec.visit_date or 'N/A',
                )
            )
        return records

    def write(self, vals):
        # Non-managers cannot edit visit lines of plans waiting for approval
        is_manager = self.env.user.has_group(
            'sales_visit_plan.group_sales_manager'
        )
        if not is_manager:
            for line in self:
                if line.plan_id.state == 'to_approve':
                    raise UserError(_(
                        'The plan "%(plan)s" is waiting for approval.\n'
                        'You cannot modify its visit lines. Please ask '
                        'your manager to move the plan back to "New" '
                        'if changes are needed.',
                        plan=line.plan_id.name,
                    ))
        coords_updated = self._apply_location_url_to_vals(vals)
        url_updated = 'location_url' in vals
        date_updated = 'visit_date' in vals
        # If dragged from first_visit → repeat_visit without the wizard,
        # log a note in chatter so the cycle is still tracked
        converted_via_drag = (
            vals.get('visit_stage') == 'repeat_visit'
            and not self.env.context.get('_convert_to_repeat')
        )
        res = super().write(vals)
        if coords_updated or url_updated:
            self._sync_coords_to_partner()
        if date_updated:
            self._update_visit_activity()
        if converted_via_drag:
            self.filtered(lambda r: r.visit_stage == 'repeat_visit').message_post(
                body=_(
                    'Visit moved to Repeat Visit via drag by %(user)s.\n'
                    'Use "Convert to Repeat" button for date/reason.',
                    user=self.env.user.name,
                )
            )
        return res

    def _apply_location_url_to_vals(self, vals):
        """If *vals* contains a location_url, parse it and inject
        lat/lng + reverse-geocoded location_details.
        Returns True when coordinates were changed or added."""
        url = vals.get('location_url')
        if url:
            lat, lng = None, None
            if 'partner_latitude' not in vals:
                lat, lng = self._parse_google_maps_url(url)
                if lat is not None:
                    vals['partner_latitude'] = lat
                    vals['partner_longitude'] = lng
            else:
                lat, lng = vals.get('partner_latitude'), vals.get('partner_longitude')
            # Always reverse-geocode and append when we have coordinates
            if lat is not None:
                addr = self._reverse_geocode(lat, lng)
                if addr:
                    existing = self.location_details if self.ids else False
                    vals['location_details'] = self._append_to_html_list(
                        existing, addr
                    )
                return True
        return 'partner_latitude' in vals or 'partner_longitude' in vals

    def _sync_coords_to_partner(self):
        """Mirror the lineʼs coordinates, location URL, and the reverse-
        geocoded address onto res.partner so the map popup shows a real
        human-readable address."""
        for line in self:
            if not line.partner_latitude or not line.partner_longitude:
                continue
            partner_vals = {
                'partner_latitude': line.partner_latitude,
                'partner_longitude': line.partner_longitude,
            }
            if line.location_url:
                partner_vals['website'] = line.location_url

            # Build street address: prefer reverse-geocoded address,
            # fall back to pharmacy_name
            address = self._reverse_geocode(
                line.partner_latitude, line.partner_longitude
            )
            if address:
                partner_vals['street'] = address
            elif line.pharmacy_name:
                partner_vals['street'] = line.pharmacy_name
            elif line.location_url:
                partner_vals['street'] = line.location_url

            if line.partner_id:
                line.partner_id.write(partner_vals)
            else:
                # Create a minimal partner so the map view can show the pin
                # (the map requires res_partner to function)
                partner = self.env['res.partner'].create({
                    'name': line.doctor_name,
                    'phone': line.phone or '',
                    'company_type': 'person',
                    **partner_vals,
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
    # Activity scheduling on the visit line
    # ------------------------------------------------------------
    def _update_visit_activity(self):
        """Create or update a To-Do activity on the visit line itself.
        Deletes all previous To-Do activities on this line (and any legacy
        ones on the parent plan), then creates a fresh one.  Ensures there
        is always exactly ONE activity per line matching the latest visit_date.
        If visit_date is empty all old activities are simply removed.
        """
        Activity = self.env['mail.activity']
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False
        )
        if not activity_type:
            return

        for line in self:
            # Delete ALL previous To-Do activities linked to this line
            # (and any legacy ones on the parent plan from older code)
            old = Activity.search([
                ('activity_type_id', '=', activity_type.id),
                '|',
                '&', ('res_model', '=', 'sales.plan.line'), ('res_id', '=', line.id),
                '&', ('res_model', '=', 'sales.plan'), ('res_id', '=', line.plan_id.id),
            ])
            old.unlink()

            # Nothing to schedule if no date
            if not line.visit_date or not line.plan_id:
                continue

            # Create a fresh activity on the visit line
            line.activity_schedule(
                activity_type_id=activity_type.id,
                user_id=line.plan_id.user_id.id,
                date_deadline=line.visit_date,
                summary=_('Scheduled Visit: %(doctor)s', doctor=line.doctor_name),
                note=_(
                    'Doctor: %(doctor)s\n'
                    'Pharmacy: %(pharmacy)s\n'
                    'Phone: %(phone)s\n'
                    'Date: %(date)s',
                    doctor=line.doctor_name,
                    pharmacy=line.pharmacy_name or 'N/A',
                    phone=line.phone or 'N/A',
                    date=line.visit_date,
                ),
            )

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
        """Open the convert-to-repeat wizard."""
        self.ensure_one()
        if self.visit_stage != 'first_visit':
            raise UserError(
                _('Only "First Visit" lines can be converted to repeat visits.')
            )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Convert to Repeat Visit'),
            'res_model': 'sales.visit.line.convert.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_id': self.id,
                'default_new_date': fields.Date.today(),
            },
        }

    def onClickGetLocation(self):
        """Dummy method — the real work is done client-side by the JS
        capture-phase click listener.  This exists only to satisfy the
        view validator (button type="object" requires a server method)."""
        return {'type': 'ir.actions.act_window_close'}


class SalesVisitLineConvertWizard(models.TransientModel):
    _name = 'sales.visit.line.convert.wizard'
    _description = 'Convert Visit to Repeat Wizard'

    line_id = fields.Many2one(
        'sales.plan.line', string='Visit Line', required=True, ondelete='cascade',
    )
    new_date = fields.Date(
        string='New Scheduled Date', required=True,
    )
    reason = fields.Text(
        string='Reason for Repeat Visit',
        help='Explain why this visit is being converted to a repeat visit.',
    )

    def action_confirm_convert(self):
        """Convert the visit to repeat, update the date, and log the reason."""
        self.ensure_one()
        line = self.line_id
        if line.visit_stage != 'first_visit':
            raise UserError(_('Only "First Visit" lines can be converted.'))

        # Update the visit (with context key to bypass the drag-block)
        vals = {'visit_stage': 'repeat_visit'}
        if self.new_date:
            vals['visit_date'] = self.new_date
        line.with_context(_convert_to_repeat=True).write(vals)

        # Log in chatter
        body = _(
            'Visit converted to Repeat Visit by %(user)s.\n'
            'New Date: %(date)s\n'
            'Reason: %(reason)s',
            user=self.env.user.name,
            date=self.new_date or line.visit_date,
            reason=self.reason or _('No reason provided.'),
        )
        line.message_post(body=body)
