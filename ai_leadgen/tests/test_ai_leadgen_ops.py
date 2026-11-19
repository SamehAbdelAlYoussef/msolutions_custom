import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user

from odoo.addons.base.tests.common import BaseCommon

ENGINE = 'odoo.addons.ai_leadgen.models.leadgen_source.LeadgenSource._run_engine'


def cand(n, status='rejected', reason='out_of_scope', source='tenders', hours_old=2, **kw):
    published = (datetime.utcnow() - timedelta(hours=hours_old)).isoformat(timespec='seconds')
    base = {'url': f'https://src.example/{n}', 'title': f'Request {n}', 'source': source, 'source_name': 'Feed', 'status': status, 'reason': reason,
            'ai_category': '', 'intent_level': '', 'buyer_intent': None, 'score': None, 'country': 'Egypt', 'language': 'ar', 'arab': True, 'published_at': published,
            'discovered_at': '2026-11-14T10:00:00+00:00', 'phones_found': 0, 'emails_found': 0, 'phones_verified': 0, 'emails_verified': 0, 'attempts': 0,
            'next_retry': None, 'odoo_id': None, 'det_category': '', 'permission': 'permitted', 'excerpt': 'original text'}
    base.update(kw)
    return base


SNAPSHOT = {
    'generated_at': '2026-11-14T12:00:00',
    'sources': [
        {'name': 'tenders', 'enabled': True, 'configured': 1, 'permitted': 1, 'status': 'ok', 'detail': '35 open tenders', 'last_run_at': '2026-11-14T11:00:00+00:00',
         'last_success_at': '2026-11-14T11:00:00+00:00', 'last_count': 35, 'failures': 0},
        {'name': 'feeds', 'enabled': True, 'configured': 0, 'permitted': 0, 'status': 'not_configured', 'detail': 'no feeds configured', 'last_run_at': None,
         'last_success_at': None, 'last_count': 0, 'failures': 0},
    ],
    'candidates': [
        cand(1, 'pushed', 'ok', arab=True, ai_category='odoo_buyer', intent_level='explicit_buyer', buyer_intent=1, score=10, emails_found=1, emails_verified=1),
        cand(2, 'rejected', 'no_contact_found', ai_category='erp_buyer', intent_level='strong_intent', buyer_intent=1, score=9),
        cand(3, 'error', 'retry_exhausted:ai_unavailable', attempts=6),
        cand(4, 'new', '', hours_old=30),
        cand(5, 'stale', 'older_than_window', hours_old=24 * 20),
        cand(6, 'duplicate', 'duplicate_content_of:x'),
        cand(7, 'rejected', 'outside_arab_countries', arab=False, country='Germany', language='en'),
        cand(8, 'rejected', 'country_unknown', arab=None, country='', language='en'),
    ],
}


class EnginePresent:
    """These tests replace the engine call itself, so they pretend an engine is installed (it is not, on a SaaS server that only ships the module)."""

    def setUp(self):
        super().setUp()
        patcher = patch('odoo.addons.ai_leadgen.models.ai_leadgen_run.AiLeadgenRun._engine_available', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestAiLeadgenOps(EnginePresent, BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='ai_leadgen_ops_mgr', groups='sales_team.group_sale_manager')
        cls.plain = new_test_user(cls.env, login='ai_leadgen_ops_plain', groups='base.group_user')
        cls.Candidate = cls.env['ai.leadgen.candidate']

    def _isolate(self):
        """The database may already hold the REAL mirror of the engine (hundreds of candidates). The dashboard counts everything, so each test starts
        from an empty mirror inside its own transaction: the real records come back untouched when the test ends."""
        self.env['ai.leadgen.candidate'].sudo().search([]).unlink()
        self.env['ai.leadgen.source.health'].sudo().search([]).unlink()

    def sync(self, snapshot=SNAPSHOT):
        with patch(ENGINE, return_value=(0, json.dumps(snapshot), '')):
            return self.Candidate._sync_from_engine()

    def test_sync_mirrors_candidates_sources_and_is_idempotent(self):
        self._isolate()
        self.assertTrue(self.sync())
        self.assertEqual(self.Candidate.search_count([('url', 'like', 'https://src.example/')]), 8)
        self.sync()                                                       # a second sync must not create anything new
        self.assertEqual(self.Candidate.search_count([('url', 'like', 'https://src.example/')]), 8)
        tenders = self.env['ai.leadgen.source.health'].search([('name', '=', 'tenders')])
        self.assertEqual((tenders.status, tenders.last_count, tenders.permitted), ('ok', 35, 1))
        first = self.Candidate.search([('url', '=', 'https://src.example/1')])
        self.assertEqual((first.state, first.intent_level, first.intent_category, first.freshness, first.emails_verified),
                         ('pushed', 'explicit_buyer', 'odoo_buyer', '24h', 1))
        self.assertEqual(self.Candidate.search([('url', '=', 'https://src.example/4')]).freshness, '7d')
        self.assertEqual(self.Candidate.search([('url', '=', 'https://src.example/5')]).freshness, 'older')

    def test_region_marks_arab_non_arab_and_unknown_countries(self):
        self._isolate()
        self.sync()
        region = lambda n: self.Candidate.search([('url', '=', f'https://src.example/{n}')]).region  # noqa: E731
        self.assertEqual((region(1), region(7), region(8)), ('arab', 'non_arab', 'unknown'))
        arab_view = self.Candidate.search([('region', '=', 'arab')])
        self.assertNotIn('https://src.example/7', arab_view.mapped('url'))
        self.assertIn('https://src.example/1', arab_view.mapped('url'))

    def test_dashboard_numbers_come_from_the_synced_candidates(self):
        self._isolate()
        self.sync()
        dash = self.env['ai.leadgen.dashboard']._the_dashboard()
        self.assertEqual((dash.leads_created, dash.qualified, dash.odoo_buyers, dash.erp_buyers), (1, 2, 1, 1))
        self.assertEqual((dash.failed, dash.duplicates, dash.rejected), (1, 1, 1))     # figures count the Arab world only
        self.assertEqual((dash.discovered, dash.outside_region, dash.unknown_region), (6, 1, 1))   # Germany and the unknown English one are listed apart, never counted as leads
        self.assertEqual((dash.emails_verified, dash.phones_verified), (1, 0))
        self.assertIn('no_contact_found: 1', dash.rejection_reasons)
        self.assertEqual(dash.sources_total, 2)
        self.assertEqual((dash.sources_connected, dash.sources_unconfigured), (1, 1))      # an unconfigured source is never reported as connected
        self.assertTrue(dash.last_sync and dash.last_sync != 'never')

    def test_a_failed_sync_is_reported_not_raised(self):
        with patch(ENGINE, return_value=(1, '', 'engine exploded')):
            self.assertFalse(self.Candidate._sync_from_engine())
        dash = self.env['ai.leadgen.dashboard']._the_dashboard()
        self.assertIn('engine exploded', dash.last_error)

    def test_only_failed_candidates_can_be_restarted_and_nothing_is_duplicated(self):
        self.sync()
        failed = self.Candidate.search([('url', '=', 'https://src.example/3')])
        rejected = self.Candidate.search([('url', '=', 'https://src.example/2')])
        with self.assertRaises(UserError):
            rejected.action_retry()
        calls = []

        def engine(args, payload=None, timeout=60, uncapped=False):
            calls.append(args)
            return (0, json.dumps({'result': 'queued'}), '') if args[0] == 'retry-candidate' else (0, json.dumps(SNAPSHOT), '')

        with patch(ENGINE, side_effect=engine):
            failed.action_retry()
        self.assertEqual(calls[0], ['retry-candidate', '--url', 'https://src.example/3'])
        self.assertEqual(self.Candidate.search_count([('url', 'like', 'https://src.example/')]), 8)

    def test_run_now_starts_a_run_over_all_enabled_sources(self):
        with patch('odoo.addons.ai_leadgen.models.ai_leadgen_run.AiLeadgenRun.action_start', return_value=True) as start:
            action = self.env['ai.leadgen.dashboard']._the_dashboard().action_run_ingestion()
        run = self.env['ai.leadgen.run'].browse(action['res_id'])
        self.assertEqual((run.source, run.hours), ('all', 168))
        start.assert_called_once()

    def test_only_the_three_real_sources_can_be_run_and_the_scheduled_run_is_off_by_default(self):
        options = {v for v, _ in self.env['ai.leadgen.run']._fields['source'].selection}
        self.assertEqual(options, {'all', 'community', 'telegram', 'maps'})
        self.assertFalse(self.env.ref('ai_leadgen.ir_cron_scheduled_run').active)
        self.assertTrue(self.env.ref('ai_leadgen.ir_cron_sync_ops').active)
        self.assertFalse(self.env.ref('ai_leadgen.ir_cron_ingest_run', raise_if_not_found=False))     # the extra ingestion cron is gone
        dash = self.env['ai.leadgen.dashboard']._the_dashboard()
        self.assertIn('synchronize the operations dashboard', dash.next_runs)
        self.assertNotIn('scheduled run', dash.next_runs)

    def test_rows_the_engine_no_longer_has_disappear_from_the_mirror(self):
        self._isolate()
        self.sync()
        self.assertEqual(self.Candidate.search_count([('url', 'like', 'https://src.example/')]), 8)
        smaller = {**SNAPSHOT, 'sources': SNAPSHOT['sources'][:1], 'candidates': SNAPSHOT['candidates'][:2]}      # a removed source took its rows with it
        self.sync(smaller)
        self.assertEqual(self.Candidate.search_count([('url', 'like', 'https://src.example/')]), 2)
        self.assertEqual(self.env['ai.leadgen.source.health'].search_count([]), 1)

    def test_access_rights(self):
        self.sync()
        self.Candidate.with_user(self.manager).search([])                  # managers can read
        with self.assertRaises(AccessError):
            self.Candidate.with_user(self.plain).search([])
        with self.assertRaises(AccessError):
            self.env['ai.leadgen.source.health'].with_user(self.plain).search([])
        with self.assertRaises(AccessError):
            self.env['ai.leadgen.dashboard'].with_user(self.plain).search([])
        with self.assertRaises(AccessError):                               # nobody edits the mirror by hand
            self.Candidate.with_user(self.manager).search([], limit=1).write({'state': 'pushed'})


class TestRemoteEngine(BaseCommon):
    """The engine may run in its own container, away from this server: it reports to Odoo over XML-RPC instead of Odoo reading its files."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Candidate = cls.env['ai.leadgen.candidate']
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.env['ir.config_parameter'].sudo().set_param('ai_leadgen.engine_path', '/nonexistent/engine')   # no engine on this server

    def _isolate(self):
        self.Candidate.sudo().search([]).unlink()
        self.env['ai.leadgen.source.health'].sudo().search([]).unlink()

    def test_a_reporting_engine_fills_the_dashboard_and_the_local_cron_keeps_its_hands_off(self):
        self._isolate()
        self.assertFalse(self.env['ai.leadgen.run']._engine_available())
        self.assertTrue(self.Candidate.remote_push_snapshot(SNAPSHOT))
        self.assertEqual(self.Candidate.search_count([]), len(SNAPSHOT['candidates']))
        self.assertFalse(self.params.get_param('ai_leadgen.ops_last_error'))   # an empty parameter is removed by Odoo
        self.assertTrue(self.Candidate._sync_from_engine())                       # no local engine, but one is reporting: no overwrite
        self.assertFalse(self.params.get_param('ai_leadgen.ops_last_error'))   # an empty parameter is removed by Odoo
        self.assertEqual(self.Candidate.search_count([]), len(SNAPSHOT['candidates']))

    def test_a_silent_engine_is_reported_after_fifteen_minutes(self):
        self._isolate()
        self.Candidate.remote_push_snapshot(SNAPSHOT)
        old = (datetime.utcnow() - timedelta(minutes=16)).strftime('%Y-%m-%d %H:%M:%S')
        self.params.set_param('ai_leadgen.ops_remote_push', old)
        self.assertFalse(self.Candidate._sync_from_engine())
        self.assertIn('none has reported', self.params.get_param('ai_leadgen.ops_last_error'))

    def test_only_an_administrator_may_report(self):
        plain = new_test_user(self.env, login='ai_leadgen_remote_plain', groups='sales_team.group_sale_manager')
        with self.assertRaises(AccessError):
            self.Candidate.with_user(plain).remote_push_snapshot(SNAPSHOT)
        with self.assertRaises(UserError):
            self.Candidate.remote_push_snapshot(['not', 'a', 'dict'])
