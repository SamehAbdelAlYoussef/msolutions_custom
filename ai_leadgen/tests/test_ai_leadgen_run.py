import json
import signal
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user

from odoo.addons.base.tests.common import BaseCommon

RUN_MODULE = 'odoo.addons.ai_leadgen.models.ai_leadgen_run'


class TestAiLeadgenRun(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='ai_leadgen_manager', groups='sales_team.group_sale_manager')
        cls.env = cls.env(user=cls.manager)

        cls.engine_dir = Path(tempfile.mkdtemp(prefix='ai_leadgen_engine_'))
        (cls.engine_dir / 'main.py').write_text('print("fake engine")\n')
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('ai_leadgen.engine_path', str(cls.engine_dir))
        params.set_param('ai_leadgen.python_path', sys.executable)

        cls.work_dir = Path(tempfile.mkdtemp(prefix='ai_leadgen_run_'))

    def _running_run(self, summary=None, log_text=None, pid=0):
        """A run already marked as running, with its result files in a temp folder."""
        run = self.env['ai.leadgen.run'].create({'source': 'maps'})
        summary_path = self.work_dir / f'run_{run.id}.json'
        log_path = self.work_dir / f'run_{run.id}.log'
        if summary is not None:
            summary_path.write_text(json.dumps(summary))
        if log_text is not None:
            log_path.write_text(log_text)
        run.sudo().write({'pid': pid, 'summary_path': str(summary_path), 'log_path': str(log_path)})
        run.write({'state': 'running'})
        return run

    def test_start_launches_the_engine_with_the_run_options(self):
        self.env['ir.config_parameter'].sudo().set_param('ai_leadgen.gemini_api_key', 'test-gemini-key')
        run = self.env['ai.leadgen.run'].create({'source': 'maps', 'dry_run': True, 'limit': 5, 'hours': 12})
        with patch(f'{RUN_MODULE}.subprocess.Popen') as popen:
            popen.return_value = MagicMock(pid=4242)
            run.action_start()
        command = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(command[command.index('main.py'):][:2], ['main.py', 'run'])
        self.assertEqual(command[:2], ['/bin/bash', '-c'])
        self.assertIn('ulimit', command[2], "the engine must not inherit Odoo's address-space cap (Chromium needs more)")
        self.assertNotIn('--limit 5', command[2], "arguments are passed as arguments, never inside the shell string")
        self.assertIn('--dry-run', command)
        self.assertEqual(command[command.index('--platform') + 1], 'maps')
        self.assertEqual(command[command.index('--limit') + 1], '5')
        self.assertEqual(command[command.index('--hours') + 1], '12')
        self.assertEqual(kwargs['cwd'], self.engine_dir)
        self.assertFalse(kwargs.get('shell'), "the engine must never be started through a shell")
        env = kwargs['env']
        self.assertEqual(env['ODOO_DB'], self.env.cr.dbname)
        self.assertEqual(env['ODOO_USER'], self.manager.login)
        self.assertTrue(env['ODOO_API_KEY'])
        self.assertEqual(env['GEMINI_API_KEY'], 'test-gemini-key')

        self.assertEqual(run.state, 'running')
        self.assertEqual(run.sudo().pid, 4242)
        keys = self.env['res.users.apikeys'].sudo().search([('user_id', '=', self.manager.id), ('name', '=', "AI Lead Gen engine")])
        self.assertEqual(len(keys), 1, "one short-lived key per run")
        self.assertTrue(keys.expiration_date, "the engine key must expire")

    def test_the_optional_google_places_key_reaches_the_engine_only_when_set(self):
        run = self.env['ai.leadgen.run'].create({'source': 'maps'})
        with patch(f'{RUN_MODULE}.subprocess.Popen') as popen:
            popen.return_value = MagicMock(pid=1)
            run.action_start()
        self.assertNotIn('GOOGLE_PLACES_API_KEY', popen.call_args.kwargs['env'])
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ai_leadgen.google_places_api_key', 'test-places-key')
        with patch(f'{RUN_MODULE}.subprocess.Popen') as popen:
            popen.return_value = MagicMock(pid=2)
            self.env['ai.leadgen.run'].create({'source': 'maps'}).action_start()
        env = popen.call_args.kwargs['env']
        self.assertEqual(env['GOOGLE_PLACES_API_KEY'], 'test-places-key')
        self.assertNotIn('GOOGLE_CSE_API_KEY', env)                      # Google Search is gone (closed to new customers)

    def test_all_sources_means_no_platform_flag(self):
        run = self.env['ai.leadgen.run'].create({})
        with patch(f'{RUN_MODULE}.subprocess.Popen') as popen:
            popen.return_value = MagicMock(pid=1)
            run.action_start()
        self.assertNotIn('--platform', popen.call_args.args[0])
        self.assertNotIn('--dry-run', popen.call_args.args[0])

    def test_only_a_draft_run_can_start(self):
        run = self._running_run()
        with self.assertRaises(UserError):
            run.action_start()

    def test_start_explains_a_missing_engine(self):
        self.env['ir.config_parameter'].sudo().set_param('ai_leadgen.engine_path', '/nonexistent/engine')
        run = self.env['ai.leadgen.run'].create({})
        with self.assertRaises(UserError):
            run.action_start()
        self.assertEqual(run.state, 'draft')

    def test_refresh_loads_the_engine_result(self):
        lead = self.env['crm.lead'].create({'name': 'Pushed by the engine'})
        run = self._running_run(summary={
            'counters': {'scraped': 9}, 'sources': {'demo': 'ok'}, 'gate_pass_rate': 0.5, 'dry_run': False,
            'rows': [
                {'platform': 'Demo', 'title': 'Good', 'score': 9, 'outcome': 'pushed', 'reason': 'ok',
                 'contacts': 'email,phone', 'odoo_id': lead.id},
                {'platform': 'Demo', 'title': 'Stale link', 'score': 8, 'outcome': 'pushed', 'reason': 'ok',
                 'contacts': 'email', 'odoo_id': 987654321},
                {'platform': 'Demo', 'title': 'No contact', 'score': 8, 'outcome': 'discarded',
                 'reason': 'no_contact_found', 'contacts': '', 'odoo_id': None},
                {'platform': 'Demo', 'title': 'Later', 'score': None, 'outcome': 'deferred',
                 'reason': 'search_blocked', 'contacts': '', 'odoo_id': None},
            ],
        })
        run.action_refresh()
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.finished)
        self.assertEqual((run.pushed_count, run.discarded_count, run.deferred_count, run.error_count), (2, 1, 1, 0))
        by_title = {line.title: line for line in run.line_ids}
        self.assertEqual(by_title['Good'].lead_id, lead)
        self.assertFalse(by_title['Stale link'].lead_id, "a lead that no longer exists must not break the run")
        self.assertEqual(run.summary['counters'], {'scraped': 9})
        run.action_refresh()  # a finished run is left alone
        self.assertEqual(len(run.line_ids), 4)

    def test_refresh_keeps_waiting_while_the_engine_is_alive(self):
        run = self._running_run(pid=1234)
        with patch.object(type(run), '_engine_process', return_value=MagicMock()):
            run.action_refresh()
        self.assertEqual(run.state, 'running')

    def test_refresh_fails_the_run_when_the_engine_died_silently(self):
        run = self._running_run(log_text='Traceback ...\nboom', pid=2147483000)
        run.action_refresh()
        self.assertEqual(run.state, 'failed')
        self.assertIn('boom', run.log)

    def test_an_error_result_fails_the_run(self):
        run = self._running_run(summary={'error': 'RuntimeError: no provider'}, log_text='see log')
        run.action_refresh()
        self.assertEqual(run.state, 'failed')

    def test_stop_ends_the_engine_process_group(self):
        run = self._running_run(pid=111)
        with (
            patch.object(type(run), '_engine_process', return_value=MagicMock(pid=111)),
            patch(f'{RUN_MODULE}.os.getpgid', return_value=222),
            patch(f'{RUN_MODULE}.os.killpg') as killpg,
        ):
            run.action_stop()
        killpg.assert_called_once_with(222, signal.SIGTERM)
        self.assertEqual(run.state, 'cancelled')

    def test_internal_process_fields_are_not_writable_by_managers(self):
        run = self.env['ai.leadgen.run'].create({})
        with self.assertRaises(AccessError):
            run.write({'pid': 1})

    def test_lead_keeps_the_ai_score_and_pitch(self):
        lead = self.env['crm.lead'].create({'name': 'AI lead', 'ai_score': 9, 'ai_whatsapp_pitch': 'hello'})
        self.assertEqual((lead.ai_score, lead.ai_whatsapp_pitch), (9, 'hello'))
        self.assertFalse(lead.copy().ai_score, "an AI score does not follow a duplicated lead")
