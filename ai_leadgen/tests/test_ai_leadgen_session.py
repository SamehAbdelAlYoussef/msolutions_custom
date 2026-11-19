import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import new_test_user

from odoo.addons.base.tests.common import BaseCommon

RUN_MODULE = 'odoo.addons.ai_leadgen.models.ai_leadgen_run'
SESSION_MODULE = 'odoo.addons.ai_leadgen.models.ai_leadgen_session'

REPORT = [
    {'platform': 'facebook', 'state': 'connected', 'age_days': 3.5, 'needed': True},
    {'platform': 'linkedin', 'state': 'missing', 'age_days': None, 'needed': True},
]


def engine_answer(rows=REPORT):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(rows), stderr='')


class TestAiLeadgenSession(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='ai_leadgen_session_mgr', groups='sales_team.group_sale_manager')
        cls.env = cls.env(user=cls.manager)
        cls.engine_dir = Path(tempfile.mkdtemp(prefix='ai_leadgen_engine_'))
        (cls.engine_dir / 'main.py').write_text('print("fake engine")\n')
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('ai_leadgen.engine_path', str(cls.engine_dir))
        params.set_param('ai_leadgen.python_path', sys.executable)
        cls.facebook = cls.env.ref('ai_leadgen.ai_leadgen_session_facebook')
        cls.linkedin = cls.env.ref('ai_leadgen.ai_leadgen_session_linkedin')

    def test_status_comes_from_the_engine_for_each_platform(self):
        with patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()) as run:
            states = {s.platform: (s.state, s.age_days, s.needed) for s in self.facebook | self.linkedin}
        self.assertEqual(states['facebook'], ('connected', 3.5, True))
        self.assertEqual(states['linkedin'], ('missing', 0.0, True))
        self.assertEqual(run.call_args.args[0][1:], ['main.py', 'sessions', '--json'])

    def test_an_unreadable_engine_shows_unknown_instead_of_breaking_the_screen(self):
        with patch(f'{SESSION_MODULE}.subprocess.run', side_effect=subprocess.TimeoutExpired('x', 1)):
            self.assertEqual(self.facebook.state, 'unknown')

    def test_authorize_opens_the_login_window_for_that_platform(self):
        with (
            patch.dict('os.environ', {'DISPLAY': ':0'}),
            patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()),
            patch(f'{SESSION_MODULE}.subprocess.Popen') as popen,
        ):
            popen.return_value = MagicMock(pid=777)
            action = self.linkedin.action_authorize()
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ['/bin/bash', '-c'])
        self.assertIn('ulimit', command[2], "Chromium needs more memory than Odoo's cap allows")
        self.assertEqual(command[command.index('main.py'):][:4], ['main.py', 'login', '--platform', 'linkedin'])
        self.assertFalse(popen.call_args.kwargs.get('shell'))
        self.assertNotIn('ODOO_API_KEY', popen.call_args.kwargs['env'], "logging in needs no Odoo key")
        self.assertEqual(self.linkedin.sudo().login_pid, 777)
        self.assertEqual(action['tag'], 'display_notification')

    def test_authorize_without_a_screen_tells_what_to_do(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(UserError) as err:
            self.facebook.action_authorize()
        self.assertIn('main.py login --platform facebook', str(err.exception))

    def test_state_is_logging_in_while_the_window_is_open(self):
        with (
            patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()),
            patch.object(type(self.linkedin), '_login_process', return_value=MagicMock()),
        ):
            self.assertEqual(self.linkedin.state, 'logging_in')

    def test_missing_platforms_lists_only_the_ones_the_groups_need(self):
        rows = [dict(REPORT[1]), dict(REPORT[0], state='missing', needed=False)]
        with patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer(rows)):
            self.assertEqual(self.env['ai.leadgen.session']._missing_platforms(), ['LinkedIn'])

    def test_deep_sync_needs_authorization_first(self):
        with (
            patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()),
            self.assertRaises(UserError) as err,
        ):
            self.env['ai.leadgen.run'].action_deep_sync()
        self.assertIn('LinkedIn', str(err.exception))

    def test_deep_sync_starts_a_community_run(self):
        connected = [dict(r, state='connected') for r in REPORT]
        with (
            patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer(connected)),
            patch(f'{RUN_MODULE}.subprocess.Popen') as popen,
        ):
            popen.return_value = MagicMock(pid=4242)
            action = self.env['ai.leadgen.run'].action_deep_sync()
        run = self.env['ai.leadgen.run'].browse(action['res_id'])
        self.assertEqual((run.source, run.hours, run.state), ('community', 48, 'running'))
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('--platform') + 1], 'community')

    def test_a_draft_community_run_warns_about_missing_authorization(self):
        run = self.env['ai.leadgen.run'].create({'source': 'community'})
        with patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()):
            self.assertIn('LinkedIn', run.session_warning)
        maps_run = self.env['ai.leadgen.run'].create({'source': 'maps'})
        with patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer()):
            self.assertFalse(maps_run.session_warning)

    def test_a_platform_warning_shows_paused_until_and_blocks_deep_sync(self):
        rows = [dict(REPORT[0], paused_until='2026-11-15T08:30+00:00'), dict(REPORT[1], state='connected', paused_until='2026-11-15T08:30+00:00')]
        with patch(f'{SESSION_MODULE}.subprocess.run', return_value=engine_answer(rows)):
            self.assertEqual(self.facebook.state, 'paused')
            self.assertEqual(str(self.facebook.paused_until), '2026-11-15 08:30:00')
            with self.assertRaises(UserError) as err:
                self.env['ai.leadgen.run'].action_deep_sync()
        self.assertIn('paused until', str(err.exception))

    def test_a_blocked_source_in_a_finished_run_is_logged_as_an_error(self):
        run = self.env['ai.leadgen.run'].create({'source': 'community'})
        summary = Path(tempfile.mkdtemp()) / 'summary.json'
        summary.write_text(json.dumps({'counters': {}, 'sources': {'community': 'blocked:session_wall'}, 'rows': []}))
        run.sudo().write({'summary_path': str(summary)})
        run.write({'state': 'running'})
        with self.assertLogs(RUN_MODULE, level='ERROR') as logs:
            run.action_refresh()
        self.assertIn('AI LEAD GEN WARNING', logs.output[0])
        self.assertEqual(run.state, 'done')
