import json
import random
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

COMMENT_TIERS = [('high_lead', "Service request"), ('inquiry', "Technical question"), ('none', "No reply")]
COMMENT_STATES = [
    ('none', "Not planned"), ('queued', "Queued"), ('posted', "Posted"), ('skipped', "Skipped"), ('failed', "Failed"),
    ('paused_risk', "Paused (risk)"),
]
COMMENT_DELAY_MINUTES = (10, 20)  # a reply is never instant and never late: a random moment in this window after the post was read
REQUEUE_MAX_AGE_HOURS = 48  # after a pause, older posts are not worth a reply any more
STATE_PARAM = 'ai_leadgen.comment_state'
KILL_PARAM = 'ai_leadgen.comment_kill'
RESUME_PARAM = 'ai_leadgen.comment_resume_token'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    ai_score = fields.Integer(string="AI Score", copy=False, help="Fit with our services, from 1 to 10.")
    ai_whatsapp_pitch = fields.Text(string="WhatsApp Pitch", copy=False)
    ai_intent_category = fields.Selection(
        [
            ('odoo_buyer', "Explicit Odoo Buyer"),
            ('erp_buyer', "ERP Buyer"),
            ('software_dev_buyer', "Software Development Buyer"),
            ('ai_automation_buyer', "AI Automation Buyer"),
            ('maintenance_customization_buyer', "Maintenance / Customization Buyer"),
            ('unqualified', "Unqualified Candidate"),
        ],
        string="Intent Category", copy=False, index=True,
    )
    ai_intent_level = fields.Selection(
        [('explicit_buyer', "Explicit buyer"), ('strong_intent', "Strong purchase intent"), ('general_prospect', "General prospect"),
         ('unqualified', "Unqualified"), ('advertisement', "Advertisement / spam")],
        string="Intent Level", copy=False)
    ai_contact_status = fields.Char(
        string="Contact Verification", copy=False,
        help="What was actually checked. A valid phone number is not a confirmed WhatsApp account.")
    ai_source_published = fields.Datetime(string="Request Published", copy=False, help="When the source published the request (UTC).")
    fb_post_url = fields.Char(string="Post Link", copy=False)
    fb_comment_draft = fields.Text(string="Reply", copy=False, help="The comment exactly as it is posted. You can edit it while it is queued.")
    fb_comment_tier = fields.Selection(COMMENT_TIERS, string="Reply Priority", copy=False, default='none')
    fb_comment_status = fields.Selection(COMMENT_STATES, string="Reply Status", copy=False, default='none', tracking=True)
    fb_comment_scheduled_at = fields.Datetime(string="Reply Planned At", copy=False)
    fb_comment_posted_at = fields.Datetime(string="Reply Posted At", copy=False)
    fb_comment_error = fields.Text(string="Reply Problem", copy=False)

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    @api.model
    def _fb_comment_slot(self):
        return fields.Datetime.now() + timedelta(seconds=random.randint(COMMENT_DELAY_MINUTES[0] * 60, COMMENT_DELAY_MINUTES[1] * 60))

    def action_fb_comment_queue(self):
        """Plan (or plan again) the reply: the engine posts it at a random moment 10 to 20 minutes from now."""
        for lead in self:
            if not lead.fb_post_url or not (lead.fb_comment_draft or '').strip():
                raise UserError(self.env._("A reply needs the post link and the reply text."))
            if lead.fb_comment_status == 'posted':
                raise UserError(self.env._("This reply is already posted."))
            if self._fb_comment_pause_reason():
                raise UserError(self.env._("Replies are stopped for safety. Lift the stop on the Operations screen first."))
        self.write({'fb_comment_status': 'queued', 'fb_comment_scheduled_at': self._fb_comment_slot(), 'fb_comment_error': False})
        for lead in self.filtered(lambda lead: lead.fb_comment_tier in (False, 'none')):
            lead.fb_comment_tier = 'high_lead'  # a reply asked for by hand is served first
        return True

    def action_fb_comment_skip(self):
        self.filtered(lambda lead: lead.fb_comment_status != 'posted').write(
            {'fb_comment_status': 'skipped', 'fb_comment_error': self.env._("Skipped by %s", self.env.user.name)})
        return True

    # -------------------------------------------------------------------------
    # ENGINE API (XML-RPC, administrator only)
    # -------------------------------------------------------------------------

    @api.model
    def _ai_leadgen_check_engine(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._("Only an administrator can drive the lead-generation engine."))

    @api.model
    def _fb_comment_pause_reason(self):
        return self.env['ir.config_parameter'].sudo().get_param(KILL_PARAM) or False

    @api.model
    def ai_leadgen_engine_config(self):
        """Everything the engine needs to write and plan replies: the switches, the contact lines, the style guide and good examples."""
        self._ai_leadgen_check_engine()
        get = self.env['ir.config_parameter'].sudo().get_param
        advanced = self.sudo().search(
            [('fb_comment_status', '=', 'posted'), ('fb_comment_draft', '!=', False), ('stage_id', 'not in', self._ai_leadgen_early_stages().ids)],
            order='fb_comment_posted_at desc', limit=5)
        return {
            'auto_comment_enabled': get('ai_leadgen.auto_comment_enabled') in ('True', '1', 'true'),
            'max_comments_per_day': int(get('ai_leadgen.max_comments_per_day') or 10),
            'whatsapp_link': get('ai_leadgen.whatsapp_link') or '',
            'website_link': get('ai_leadgen.website_link') or '',
            'include_whatsapp': get('ai_leadgen.comment_include_whatsapp', 'True') in ('True', '1', 'true'),
            'include_website': get('ai_leadgen.comment_include_website') in ('True', '1', 'true'),
            'style_guide': get('ai_leadgen.comment_style_guide') or '',
            'contact_lines': [line.strip() for line in (get('ai_leadgen.comment_contact_lines') or '').splitlines() if line.strip()],
            'examples': [block.strip() for block in (get('ai_leadgen.comment_examples') or '').split('\n\n') if block.strip()],
            'won_examples': advanced.mapped('fb_comment_draft'),  # replies whose lead moved forward in the pipeline
            'kill': self._fb_comment_pause_reason() or '',
            'resume_token': get(RESUME_PARAM) or '',
        }

    @api.model
    def ai_leadgen_comment_queue(self, limit=10):
        """Replies that are due now, service requests first, then questions, oldest plan first."""
        self._ai_leadgen_check_engine()
        due = self.sudo().search([('fb_comment_status', '=', 'queued'), ('fb_comment_scheduled_at', '<=', fields.Datetime.now()),
                                  ('fb_post_url', '!=', False)], order='fb_comment_scheduled_at, id')
        due = due.sorted(lambda lead: (lead.fb_comment_tier != 'high_lead', lead.fb_comment_scheduled_at, lead.id))[:limit]
        return [{'id': lead.id, 'url': lead.fb_post_url, 'text': lead.fb_comment_draft or '', 'tier': lead.fb_comment_tier or 'none',
                 'scheduled_at': fields.Datetime.to_string(lead.fb_comment_scheduled_at),
                 'created_at': fields.Datetime.to_string(lead.create_date)} for lead in due]

    @api.model
    def ai_leadgen_comment_result(self, lead_id, status, error=False):
        """The engine reports what happened to one reply. A posted reply moves the lead to the 'commented' stage and is kept in the chatter."""
        self._ai_leadgen_check_engine()
        if status not in dict(COMMENT_STATES):
            raise UserError(self.env._("Unknown reply status: %s", status))
        lead = self.sudo().browse(int(lead_id)).exists()
        if not lead:
            return False
        vals = {'fb_comment_status': status, 'fb_comment_error': error or False}
        if status == 'posted':
            vals['fb_comment_posted_at'] = fields.Datetime.now()
            stage = self.env.ref('ai_leadgen.crm_stage_commented', raise_if_not_found=False)
            if stage and lead.stage_id in self._ai_leadgen_early_stages():
                vals['stage_id'] = stage.id
        lead.write(vals)
        if status == 'posted':
            lead.message_post(body=Markup("<p><b>%s</b></p><p>%s</p><p><a href=\"%s\" target=\"_blank\">%s</a></p>") % (
                self.env._("Reply posted on the Facebook post"), Markup("<br/>").join((lead.fb_comment_draft or '').splitlines()),
                lead.fb_post_url or '#', lead.fb_post_url or ''))
        return True

    @api.model
    def ai_leadgen_report_comment_state(self, state):
        """The engine's safety report (risk score, today's replies, any pause). A danger stops every waiting reply until somebody lifts the stop."""
        self._ai_leadgen_check_engine()
        if not isinstance(state, dict):
            raise UserError(self.env._("The report must be a dictionary."))
        params = self.env['ir.config_parameter'].sudo()
        was_paused = bool(json.loads(params.get_param(STATE_PARAM) or '{}').get('paused_until'))
        state = dict(state, reported_at=fields.Datetime.to_string(fields.Datetime.now()))
        params.set_param(STATE_PARAM, json.dumps(state))
        if state.get('kill'):
            params.set_param(KILL_PARAM, "%s (%s UTC)" % (str(state['kill'])[:300], state['reported_at']))
        if state.get('kill') or state.get('paused_until'):
            self.sudo().search([('fb_comment_status', '=', 'queued')]).write({
                'fb_comment_status': 'paused_risk', 'fb_comment_error': str(state.get('kill') or state.get('pause_reason') or '')[:300]})
        elif was_paused and not self._fb_comment_pause_reason():
            self._fb_comment_requeue_paused()  # a timed pause ended by itself
        return True

    @api.model
    def _fb_comment_requeue_paused(self):
        """Replies stopped by a pause go back to the queue when their post is still fresh; older ones are dropped."""
        paused = self.sudo().search([('fb_comment_status', '=', 'paused_risk')], order='id')
        fresh = paused.filtered(lambda lead: lead.create_date > fields.Datetime.now() - timedelta(hours=REQUEUE_MAX_AGE_HOURS))
        for lead in fresh:
            lead.write({'fb_comment_status': 'queued', 'fb_comment_scheduled_at': self._fb_comment_slot(), 'fb_comment_error': False})
        (paused - fresh).write({'fb_comment_status': 'skipped', 'fb_comment_error': self.env._("The post got too old while replies were stopped.")})
        return len(fresh)

    # -------------------------------------------------------------------------
    # CLEANUP
    # -------------------------------------------------------------------------

    @api.model
    def _ai_leadgen_early_stages(self):
        """The stages nobody worked on yet: New and 'commented'."""
        stages = self.env['crm.stage']
        for xmlid in ('crm.stage_lead1', 'ai_leadgen.crm_stage_commented'):
            stages |= self.env.ref(xmlid, raise_if_not_found=False) or stages
        return stages.sudo()

    @api.model
    def _cron_ai_leadgen_cleanup(self):
        """Delete the generated leads nobody picked up: older than the configured days and still in New or 'commented'.
        A lead moved to any later stage is kept for good."""
        days = int(self.env['ir.config_parameter'].sudo().get_param('ai_leadgen.auto_delete_days') or 3)
        stages = self._ai_leadgen_early_stages()
        if days <= 0 or not stages:
            return 0
        stale = self.sudo().search([('ai_score', '>', 0), ('stage_id', 'in', stages.ids),
                                    ('create_date', '<', fields.Datetime.now() - timedelta(days=days))])
        count = len(stale)
        stale.unlink()
        return count
