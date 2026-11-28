from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import new_test_user

from odoo.addons.base.tests.common import BaseCommon


class TestAiLeadgenComments(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env['crm.lead']
        cls.new_stage = cls.env.ref('crm.stage_lead1')
        cls.commented = cls.env.ref('ai_leadgen.crm_stage_commented')
        cls.qualified = cls.env.ref('crm.stage_lead2')
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param('ai_leadgen.comment_kill', '')

    def lead(self, n, **kw):
        vals = {'name': f'Request {n}', 'type': 'opportunity', 'ai_score': 9, 'stage_id': self.new_stage.id,
                'fb_post_url': f'https://www.facebook.com/groups/1/posts/{n}', 'fb_comment_draft': f'reply {n}', 'fb_comment_tier': 'high_lead'}
        vals.update(kw)
        return self.Lead.create(vals)

    def age(self, leads, days):
        leads.flush_recordset()
        self.env.cr.execute("UPDATE crm_lead SET create_date = now() at time zone 'utc' - %s * interval '1 day' WHERE id IN %s", (days, tuple(leads.ids)))
        leads.invalidate_recordset(['create_date'])

    # ------------------------------------------------------------------ planning
    def test_plan_reply_lands_between_10_and_20_minutes(self):
        lead = self.lead(1)
        before = fields.Datetime.now()
        lead.action_fb_comment_queue()
        self.assertEqual(lead.fb_comment_status, 'queued')
        delay = (lead.fb_comment_scheduled_at - before).total_seconds() / 60
        self.assertTrue(10 <= delay <= 20.1, delay)

    def test_plan_reply_needs_link_and_text(self):
        with self.assertRaises(UserError):
            self.lead(1, fb_comment_draft=False).action_fb_comment_queue()
        with self.assertRaises(UserError):
            self.lead(2, fb_comment_status='posted').action_fb_comment_queue()

    def test_plan_reply_is_refused_while_replies_are_stopped(self):
        self.params.set_param('ai_leadgen.comment_kill', 'checkpoint')
        with self.assertRaises(UserError):
            self.lead(1).action_fb_comment_queue()

    def test_skip(self):
        lead = self.lead(1, fb_comment_status='queued')
        lead.action_fb_comment_skip()
        self.assertEqual(lead.fb_comment_status, 'skipped')

    # ------------------------------------------------------------------ engine API
    def test_engine_api_is_for_administrators_only(self):
        user = new_test_user(self.env, login='seller', groups='sales_team.group_sale_manager')
        Lead = self.Lead.with_user(user)
        for call in (lambda: Lead.ai_leadgen_engine_config(), lambda: Lead.ai_leadgen_comment_queue(),
                     lambda: Lead.ai_leadgen_comment_result(1, 'posted'), lambda: Lead.ai_leadgen_report_comment_state({})):
            with self.assertRaises(AccessError):
                call()

    def test_queue_serves_service_requests_before_questions(self):
        past = fields.Datetime.now() - timedelta(minutes=5)
        question = self.lead(1, fb_comment_tier='inquiry', fb_comment_status='queued', fb_comment_scheduled_at=past - timedelta(minutes=30))
        request = self.lead(2, fb_comment_status='queued', fb_comment_scheduled_at=past)
        self.lead(3, fb_comment_status='queued', fb_comment_scheduled_at=fields.Datetime.now() + timedelta(minutes=9))  # not due yet
        self.lead(4, fb_comment_status='skipped', fb_comment_scheduled_at=past)
        queue = self.Lead.ai_leadgen_comment_queue()
        self.assertEqual([row['id'] for row in queue], [request.id, question.id])
        self.assertEqual(queue[0]['text'], 'reply 2')

    def test_posted_reply_moves_the_lead_and_is_logged(self):
        lead = self.lead(1, fb_comment_status='queued')
        self.Lead.ai_leadgen_comment_result(lead.id, 'posted')
        self.assertEqual(lead.fb_comment_status, 'posted')
        self.assertEqual(lead.stage_id, self.commented)
        self.assertTrue(lead.fb_comment_posted_at)
        self.assertIn('reply 1', lead.message_ids[0].body)

    def test_posted_reply_never_moves_a_lead_backwards(self):
        lead = self.lead(1, stage_id=self.qualified.id)
        self.Lead.ai_leadgen_comment_result(lead.id, 'posted')
        self.assertEqual(lead.stage_id, self.qualified)

    def test_failed_reply_keeps_the_reason(self):
        lead = self.lead(1, fb_comment_status='queued')
        self.Lead.ai_leadgen_comment_result(lead.id, 'failed', 'composer_not_found')
        self.assertEqual((lead.fb_comment_status, lead.fb_comment_error, lead.stage_id), ('failed', 'composer_not_found', self.new_stage))
        with self.assertRaises(UserError):
            self.Lead.ai_leadgen_comment_result(lead.id, 'invented')
        self.assertFalse(self.Lead.ai_leadgen_comment_result(10 ** 9, 'failed'))

    def test_engine_config(self):
        self.params.set_param('ai_leadgen.auto_comment_enabled', 'True')
        self.params.set_param('ai_leadgen.whatsapp_link', 'https://wa.me/201000000000')
        self.params.set_param('ai_leadgen.comment_contact_lines', 'a {whatsapp}\n\nb {whatsapp}')
        self.params.set_param('ai_leadgen.comment_examples', 'first\nline\n\nsecond')
        won = self.lead(1, fb_comment_status='posted', stage_id=self.qualified.id, fb_comment_draft='the winning reply')
        self.lead(2, fb_comment_status='posted', stage_id=self.commented.id)
        config = self.Lead.ai_leadgen_engine_config()
        self.assertTrue(config['auto_comment_enabled'])
        self.assertEqual(config['max_comments_per_day'], 10)
        self.assertEqual(config['contact_lines'], ['a {whatsapp}', 'b {whatsapp}'])
        self.assertEqual(config['examples'], ['first\nline', 'second'])
        self.assertEqual(config['won_examples'], [won.fb_comment_draft])
        self.assertTrue(config['include_whatsapp'])
        self.assertFalse(config['include_website'])

    # ------------------------------------------------------------------ danger stop
    def test_danger_stops_every_waiting_reply_until_resumed(self):
        fresh = self.lead(1, fb_comment_status='queued')
        old = self.lead(2, fb_comment_status='queued')
        posted = self.lead(3, fb_comment_status='posted')
        self.age(old, 3)
        self.Lead.ai_leadgen_report_comment_state({'score': 100, 'zone': 'danger', 'kill': 'facebook: redirected to /checkpoint'})
        self.assertEqual((fresh.fb_comment_status, old.fb_comment_status, posted.fb_comment_status), ('paused_risk', 'paused_risk', 'posted'))
        dashboard = self.env['ai.leadgen.dashboard']._the_dashboard()
        self.assertIn('checkpoint', dashboard.comment_alert)
        self.assertTrue(dashboard.comment_needs_resume)
        self.assertEqual(dashboard.comment_risk_zone, 'danger')
        self.assertIn('checkpoint', self.Lead.ai_leadgen_engine_config()['kill'])
        # a later quiet report does not lift a danger stop
        self.Lead.ai_leadgen_report_comment_state({'score': 10, 'zone': 'safe'})
        self.assertEqual(fresh.fb_comment_status, 'paused_risk')
        dashboard.action_resume_comments()
        dashboard.invalidate_recordset()
        self.assertFalse(dashboard.comment_alert)
        self.assertEqual((fresh.fb_comment_status, old.fb_comment_status), ('queued', 'skipped'))
        self.assertTrue(self.Lead.ai_leadgen_engine_config()['resume_token'])

    def test_timed_pause_ends_by_itself(self):
        lead = self.lead(1, fb_comment_status='queued')
        self.Lead.ai_leadgen_report_comment_state({'score': 70, 'zone': 'danger', 'paused_until': '2026-11-29 10:00', 'pause_reason': '3 replies failed in a row'})
        self.assertEqual(lead.fb_comment_status, 'paused_risk')
        self.assertIn('3 replies failed', self.env['ai.leadgen.dashboard']._the_dashboard().comment_alert)
        self.Lead.ai_leadgen_report_comment_state({'score': 20, 'zone': 'safe', 'posted_24h': 2, 'effective_cap': 5})
        self.assertEqual(lead.fb_comment_status, 'queued')
        dashboard = self.env['ai.leadgen.dashboard']._the_dashboard()
        dashboard.invalidate_recordset()
        self.assertEqual((dashboard.comments_today, dashboard.comments_cap_today, dashboard.comment_risk_zone), (2, 5, 'safe'))

    # ------------------------------------------------------------------ learning
    def test_learning_feedback_tells_what_became_of_each_lead(self):
        moved = self.lead(1, stage_id=self.qualified.id, fb_comment_status='posted')
        untouched = self.lead(2)
        lost = self.lead(3)
        lost.action_set_lost()
        rows = {row['id']: row for row in self.Lead.ai_leadgen_learning_feedback()}
        self.assertTrue(rows[moved.id]['advanced'])
        self.assertEqual((rows[untouched.id]['advanced'], rows[untouched.id]['lost'], rows[lost.id]['lost']), (False, False, True))
        self.Lead.ai_leadgen_report_learning('- buyers name their Odoo version')
        dashboard = self.env['ai.leadgen.dashboard']._the_dashboard()
        self.assertIn('Odoo version', dashboard.learned_notes)
        dashboard.action_forget_learning()
        dashboard.invalidate_recordset()
        self.assertFalse(dashboard.learned_notes)
        self.assertTrue(self.Lead.ai_leadgen_engine_config()['learning_reset_token'])

    # ------------------------------------------------------------------ cleanup
    def test_cleanup_deletes_only_old_untouched_generated_leads(self):
        old_new = self.lead(1)
        old_commented = self.lead(2, stage_id=self.commented.id, fb_comment_status='posted')
        old_qualified = self.lead(3, stage_id=self.qualified.id)
        old_manual = self.lead(4, ai_score=0)  # not a generated lead
        recent = self.lead(5)
        self.age(old_new | old_commented | old_qualified | old_manual, 4)
        self.age(recent, 2)
        self.assertEqual(self.Lead._cron_ai_leadgen_cleanup(), 2)
        self.assertFalse((old_new | old_commented).exists())
        self.assertEqual((old_qualified | old_manual | recent).exists(), old_qualified | old_manual | recent)

    def test_cleanup_follows_the_setting(self):
        lead = self.lead(1)
        self.age(lead, 4)
        self.params.set_param('ai_leadgen.auto_delete_days', '0')
        self.assertEqual(self.Lead._cron_ai_leadgen_cleanup(), 0)
        self.params.set_param('ai_leadgen.auto_delete_days', '7')
        self.assertEqual(self.Lead._cron_ai_leadgen_cleanup(), 0)
        self.params.set_param('ai_leadgen.auto_delete_days', '3')
        self.assertEqual(self.Lead._cron_ai_leadgen_cleanup(), 1)
        self.assertFalse(lead.exists())

    def test_cleanup_cron_exists(self):
        cron = self.env.ref('ai_leadgen.ir_cron_cleanup_leads')
        self.assertEqual((cron.interval_number, cron.interval_type, cron.active), (6, 'hours', True))

    # ------------------------------------------------------------------ settings
    def test_settings_keep_the_reply_texts(self):
        with Form(self.env['res.config.settings']) as form:
            self.assertEqual(form.ai_leadgen_max_comments_per_day, 10)
            self.assertEqual(form.ai_leadgen_auto_delete_days, 3)
            self.assertIn('{whatsapp}', form.ai_leadgen_comment_contact_lines)
            form.ai_leadgen_whatsapp_link = 'https://wa.me/201017729427'
            form.ai_leadgen_comment_style_guide = 'short\nand direct'
            form.ai_leadgen_auto_comment_enabled = True
        form.record.execute()
        self.assertEqual(self.params.get_param('ai_leadgen.comment_style_guide'), 'short\nand direct')
        config = self.Lead.ai_leadgen_engine_config()
        self.assertEqual((config['whatsapp_link'], config['style_guide'], config['auto_comment_enabled']),
                         ('https://wa.me/201017729427', 'short\nand direct', True))
