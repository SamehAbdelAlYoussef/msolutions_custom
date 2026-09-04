import json
import re
import urllib.request

from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

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
        # Detect plans that newly change state so the right party is notified
        # whatever the path (button, statusbar, or kanban drag).
        newly_approved = self.browse()
        newly_to_approve = self.browse()
        if vals.get('state') == 'approved':
            newly_approved = self.filtered(lambda p: p.state != 'approved')
        if vals.get('state') == 'to_approve':
            newly_to_approve = self.filtered(lambda p: p.state != 'to_approve')
        res = super().write(vals)
        if newly_to_approve:
            newly_to_approve._notify_request_approval()
        if newly_approved:
            newly_approved._notify_plan_approved()
        return res

    def _notify_request_approval(self):
        """Notify the manager(s) that a plan needs approval — email logged
        in the chatter + a To-Do activity for each manager."""
        for plan in self:
            managers = plan._get_sales_managers()
            plan.message_post_with_source(
                'sales_visit_plan.email_template_approval_request',
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
                partner_ids=managers.mapped('partner_id').ids,
            )
            plan._create_manager_activity(managers)
            plan.message_post(
                body=_('Plan sent for approval by %s.') % self.env.user.name
            )

    def _notify_plan_approved(self):
        """Notify the salesperson that the plan is approved — email logged
        in the chatter + a To-Do activity + a note."""
        for plan in self:
            plan.message_post_with_source(
                'sales_visit_plan.email_template_approval_confirmation',
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
                partner_ids=plan.user_id.partner_id.ids,
            )
            plan._create_salesperson_activity()
            plan.message_post(
                body=_('Plan approved by %s.') % self.env.user.name
            )

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

        # Notifications (email logged in chatter + manager activity) are
        # handled centrally in write() so it also works via statusbar/drag.
        self.state = 'to_approve'

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

        # Notifications (email + activity + chatter) are handled centrally
        # in write() so approval works via button, statusbar, or kanban drag.
        self.state = 'approved'

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

    # ------------------------------------------------------------
    # Scheduled action: notify managers about their salespersons'
    # overdue activities (email + internal Inbox message).
    # ------------------------------------------------------------
    @api.model
    def _cron_notify_overdue_activities(self):
        """Runs daily. Collects overdue activities on sales plans / visit
        lines, groups them per manager → salesperson, and sends each
        manager a digest by email and as an internal message."""
        today = fields.Date.today()
        activities = self.env['mail.activity'].search([
            ('date_deadline', '<', today),
            ('res_model', 'in', ('sales.plan', 'sales.plan.line')),
        ])
        if not activities:
            return

        # buckets: manager.id -> {'manager': user, 'people': {sp.id: {...}}}
        buckets = {}
        for act in activities:
            plan = self._plan_of_activity(act)
            if not plan:
                continue
            managers = plan.manager_id or self._get_sales_managers_fallback()
            salesperson = act.user_id or plan.user_id
            for manager in managers:
                if not manager:
                    continue
                mbucket = buckets.setdefault(
                    manager.id, {'manager': manager, 'people': {}})
                pbucket = mbucket['people'].setdefault(
                    salesperson.id, {'user': salesperson, 'items': []})
                pbucket['items'].append({
                    'summary': (act.summary
                                or act.activity_type_id.display_name
                                or _('Activity')),
                    'record': self._activity_record_name(act, plan),
                    'deadline': act.date_deadline,
                    'days': (today - act.date_deadline).days,
                })

        for data in buckets.values():
            self._send_overdue_digest(data['manager'], data['people'])

    def _plan_of_activity(self, act):
        """Return the sales.plan tied to an activity (directly or via line)."""
        if act.res_model == 'sales.plan':
            return self.env['sales.plan'].browse(act.res_id).exists()
        line = self.env['sales.plan.line'].browse(act.res_id).exists()
        return line.plan_id if line else self.env['sales.plan']

    def _activity_record_name(self, act, plan):
        """Human label for the record the activity sits on."""
        if act.res_model == 'sales.plan.line':
            line = self.env['sales.plan.line'].browse(act.res_id).exists()
            if line:
                return _('%(line)s (Plan: %(plan)s)',
                         line=line.display_name, plan=plan.name)
        return _('Plan: %s') % plan.name

    def _get_sales_managers_fallback(self):
        """Managers to use when a plan has no explicit manager_id."""
        group = self.env.ref(
            'sales_visit_plan.group_sales_manager', raise_if_not_found=False)
        return group.user_ids if group else self.env['res.users']

    def _send_overdue_digest(self, manager, people):
        """Build and send the overdue digest to one manager: email +
        internal Inbox message."""
        if not manager or not people:
            return

        total = sum(len(p['items']) for p in people.values())
        blocks = []
        for pdata in people.values():
            sp = pdata['user']
            rows = ''.join(
                '<li>%s — <b>%s</b> '
                '<span style="color:#c92a2a;">(%s — %s day(s) late)</span></li>' % (
                    item['record'], item['summary'],
                    item['deadline'], item['days'])
                for item in pdata['items']
            )
            blocks.append(
                '<div style="margin:10px 0;">'
                '<div style="font-weight:bold;color:#087F5B;font-size:14px;">'
                '%s <span style="color:#868e96;">(%s overdue)</span></div>'
                '<ul style="margin:4px 0 0;">%s</ul></div>' % (
                    sp.display_name or _('Undefined'),
                    len(pdata['items']), rows)
            )

        body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;">'
            '<h2 style="color:#087F5B;">Overdue Visit Activities</h2>'
            '<p>Dear %(manager)s,</p>'
            '<p>The following <b>%(total)s</b> activity(ies) assigned to your '
            'team in <b>Sales Visit Plans</b> are overdue and still open:</p>'
            '%(blocks)s'
            '<p style="color:#888;font-size:12px;margin-top:20px;">'
            'Automated daily reminder from the Sales Visit Plan system.</p>'
            '</div>'
        ) % {
            'manager': manager.name,
            'total': total,
            'blocks': ''.join(blocks),
        }
        subject = _('Overdue Visit Activities — %s item(s)') % total

        # 1) Direct chat message from the System (OdooBot). This always lands
        #    in the manager's Discuss chat window regardless of their
        #    notification preference (unlike message_notify, which routes to
        #    email for "Handle by Emails" users).
        odoobot = self.env.ref('base.user_root', raise_if_not_found=False)
        if manager.partner_id and odoobot:
            channel = self.env['discuss.channel'].with_user(odoobot)._get_or_create_chat(
                partners_to=[manager.partner_id.id],
            )
            channel.with_user(odoobot).message_post(
                body=Markup(body),
                subject=subject,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

        # 2) Email — guaranteed copy in the manager's mailbox.
        if manager.email:
            self.env['mail.mail'].sudo().create({
                'subject': subject,
                'body_html': body,
                'email_to': manager.email,
                'auto_delete': True,
            }).send()


    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @api.model
    def _dashboard_data(self):
        """Single RPC call for the visit dashboard.

        Returns today's schedule for the current user as a field rep, plus
        any plans waiting for their approval as a manager.  Everything is
        aggregated here so the OWL component makes exactly one round trip.
        """
        today = fields.Date.today()
        uid = self.env.uid
        Line = self.env["sales.plan.line"]

        today_lines = Line.search(
            [("plan_id.user_id", "=", uid), ("visit_date", "=", today)],
            order="id asc",
        )
        pending_approval = self.search(
            [("manager_id", "=", uid), ("state", "=", "to_approve")],
            order="create_date desc",
        )

        completed_today = sum(
            1 for l in today_lines if l.visit_stage == "completed"
        )

        return {
            "user_name": self.env.user.name.split()[0],
            "today_iso": str(today),
            "my_plan_count": self.search_count([("user_id", "=", uid)]),
            "visits_today": len(today_lines),
            "completed_today": completed_today,
            "pending_my_approval": len(pending_approval),
            "today_lines": [
                {
                    "id": l.id,
                    "visit_type": l.visit_type,
                    "contact_name": (
                        l.doctor_name or l.pharmacy_name or l.meeting_name or ""
                    ),
                    "specialty": l.specialty_id.name if l.specialty_id else "",
                    "phone": l.phone or "",
                    "visit_stage": l.visit_stage,
                    "plan_id": l.plan_id.id,
                }
                for l in today_lines
            ],
            "pending_approval": [
                {
                    "id": p.id,
                    "name": p.name,
                    "user_name": p.user_id.name,
                    "visit_count": p.visit_count,
                    "region": p.region or "",
                }
                for p in pending_approval
            ],
        }


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
    visit_type = fields.Selection(
        selection=[
            ('doctor', 'Doctor'),
            ('pharmacy', 'Pharmacy'),
            ('meeting', 'Meeting'),
        ],
        string='Visit Type',
        default='doctor',
        required=True,
        tracking=True,
        help='Choose whether this record targets a doctor, a pharmacy, or '
             'an internal meeting. The form shows only the relevant fields '
             'for the chosen type. Internal meetings cannot be completed or '
             'turned into an opportunity.',
    )
    doctor_name = fields.Char(
        string='Doctor / Pharmacist Name',
        tracking=True,
    )
    meeting_name = fields.Char(
        string='Meeting Name',
        tracking=True,
    )
    meeting_link = fields.Char(
        string='Meeting Link',
        tracking=True,
        help='Paste the online meeting URL (Google Meet, Zoom, Teams…).',
    )
    meeting_event_id = fields.Many2one(
        'calendar.event',
        string='Calendar Meeting',
        ondelete='set null',
        copy=False,
        help='The calendar event booked for this internal meeting.',
    )
    phone = fields.Char(
        string='Phone',
        tracking=True,
    )
    specialty_id = fields.Many2one(
        'sales.doctor.specialty',
        string='Specialty',
        tracking=True,
        help='The medical specialty of the doctor being visited.',
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
    # Display name — pick the right label per visit type so meeting /
    # pharmacy records are never shown as "Unnamed".
    # ------------------------------------------------------------
    @api.depends('visit_type', 'doctor_name', 'pharmacy_name', 'meeting_name')
    def _compute_display_name(self):
        for line in self:
            if line.visit_type == 'meeting':
                line.display_name = line.meeting_name or _('Meeting')
            elif line.visit_type == 'pharmacy':
                line.display_name = line.pharmacy_name or _('Pharmacy')
            else:
                line.display_name = line.doctor_name or _('Doctor')

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
        # Book meetings on the calendar
        records._sync_meeting_calendar_event()
        # Post creation message in chatter
        for rec in records:
            if rec.visit_type == 'meeting':
                body = _(
                    'Meeting created: %(name)s\n'
                    'Link: %(link)s\n'
                    'Date: %(date)s',
                    name=rec.meeting_name or 'N/A',
                    link=rec.meeting_link or 'N/A',
                    date=rec.visit_date or 'N/A',
                )
            else:
                body = _(
                    'Visit created for %(doctor)s\n'
                    'Phone: %(phone)s\n'
                    'Pharmacy: %(pharmacy)s\n'
                    'Date: %(date)s',
                    doctor=rec.doctor_name,
                    phone=rec.phone or 'N/A',
                    pharmacy=rec.pharmacy_name or 'N/A',
                    date=rec.visit_date or 'N/A',
                )
            rec.message_post(body=body)
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
        meeting_updated = date_updated or bool(
            {'visit_type', 'meeting_name', 'meeting_link'} & set(vals)
        )
        res = super().write(vals)
        if coords_updated or url_updated:
            self._sync_coords_to_partner()
        if date_updated:
            self._update_visit_activity()
        if meeting_updated:
            self._sync_meeting_calendar_event()
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

            # Meeting lines get a meeting-named activity, others a visit one
            if line.visit_type == 'meeting':
                summary = _('Meeting: %(name)s',
                            name=line.meeting_name or _('Untitled'))
                note = _(
                    'Meeting: %(name)s\n'
                    'Link: %(link)s\n'
                    'Date: %(date)s',
                    name=line.meeting_name or 'N/A',
                    link=line.meeting_link or 'N/A',
                    date=line.visit_date,
                )
            else:
                summary = _('Scheduled Visit: %(doctor)s',
                            doctor=line.doctor_name)
                note = _(
                    'Doctor: %(doctor)s\n'
                    'Pharmacy: %(pharmacy)s\n'
                    'Phone: %(phone)s\n'
                    'Date: %(date)s',
                    doctor=line.doctor_name,
                    pharmacy=line.pharmacy_name or 'N/A',
                    phone=line.phone or 'N/A',
                    date=line.visit_date,
                )

            # Create a fresh activity on the visit line
            line.activity_schedule(
                activity_type_id=activity_type.id,
                user_id=line.plan_id.user_id.id,
                date_deadline=line.visit_date,
                summary=summary,
                note=note,
            )

    # ------------------------------------------------------------
    # Calendar booking for internal meetings
    # ------------------------------------------------------------
    def _sync_meeting_calendar_event(self):
        """Create / update / remove the calendar.event that books an
        internal meeting.  Only 'meeting' lines with a date get an event;
        switching a line away from 'meeting' (or clearing its date) drops
        the booking so the calendar stays clean."""
        Event = self.env['calendar.event']
        for line in self:
            keep = line.visit_type == 'meeting' and line.visit_date
            if not keep:
                if line.meeting_event_id:
                    line.meeting_event_id.unlink()
                continue

            start = fields.Datetime.to_datetime(line.visit_date)
            attendees = line.plan_id.user_id.partner_id
            if line.plan_id.manager_id:
                attendees |= line.plan_id.manager_id.partner_id
            vals = {
                'name': line.meeting_name or _('Internal Meeting'),
                'start': start,
                'stop': start,
                'allday': True,
                'user_id': line.plan_id.user_id.id or self.env.uid,
                'partner_ids': [(6, 0, attendees.ids)],
                'videocall_location': line.meeting_link or False,
                'description': _(
                    'Internal meeting scheduled from Sales Visit Plan "%(plan)s".',
                    plan=line.plan_id.name,
                ),
            }
            if line.meeting_event_id:
                line.meeting_event_id.write(vals)
            else:
                line.meeting_event_id = Event.create(vals)

    def unlink(self):
        # Remove any calendar bookings tied to these lines first
        self.mapped('meeting_event_id').unlink()
        return super().unlink()

    # ------------------------------------------------------------
    _MEETING_BLOCK_MSG = (
        'This is an internal meeting — not a doctor/pharmacy visit or a '
        'potential opportunity. It cannot be marked as completed or turned '
        'into a CRM lead.'
    )

    @api.constrains('visit_stage', 'visit_type')
    def _check_meeting_not_completed(self):
        for line in self:
            if line.visit_type == 'meeting' and line.visit_stage == 'completed':
                raise ValidationError(_(self._MEETING_BLOCK_MSG))

    def action_mark_completed(self):
        self.ensure_one()
        if self.visit_type == 'meeting':
            raise ValidationError(_(self._MEETING_BLOCK_MSG))
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
            'name': '%s - %s' % (
                self.doctor_name or self.pharmacy_name or _('Visit'),
                self.specialty_id.name or 'Visit',
            ),
            'partner_id': partner.id,
            'phone': self.phone,
            'type': 'opportunity',
            'visit_type': self.visit_type,
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


class SalesDoctorSpecialty(models.Model):
    _name = 'sales.doctor.specialty'
    _description = 'Doctor Specialty'
    _order = 'name'

    name = fields.Char(
        string='Specialty',
        required=True,
        translate=True,
    )
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        'unique(name)',
        'This specialty already exists.',
    )


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    visit_type = fields.Selection(
        selection=[
            ('doctor', 'Doctor'),
            ('pharmacy', 'Pharmacy'),
        ],
        string='Visit Type',
        help='Set from the Sales Visit Plan when the visit is marked as '
             'completed. Shows whether this opportunity came from a doctor '
             'or a pharmacy visit.',
    )


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
