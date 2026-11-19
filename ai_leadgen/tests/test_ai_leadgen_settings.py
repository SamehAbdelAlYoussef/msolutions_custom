import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import Form

from odoo.addons.base.tests.common import BaseCommon

ENGINE = 'odoo.addons.ai_leadgen.models.leadgen_source.LeadgenSource._run_engine'


class TestAiProviderSettings(BaseCommon):

    def _fields(self):
        return self.env['res.config.settings']._fields

    def test_provider_and_model_are_selection_fields(self):
        f = self._fields()
        self.assertEqual({v for v, _ in f['ai_leadgen_ai_provider'].selection}, {'gemini', 'deepseek'})
        self.assertEqual({v for v, _ in f['ai_leadgen_gemini_model'].selection}, {'gemini-3.5-flash-lite', 'gemini-2.5-flash'})
        self.assertEqual({v for v, _ in f['ai_leadgen_deepseek_model'].selection}, {'deepseek-flash', 'deepseek-v4-pro'})
        self.assertEqual([v for v, _ in f['ai_leadgen_deepseek_thinking'].selection], ['off', 'low', 'high', 'max'])
        self.assertEqual((f['ai_leadgen_ai_provider'].default(self.env['res.config.settings']), f['ai_leadgen_deepseek_thinking'].default(self.env['res.config.settings'])), ('gemini', 'off'))

    def test_only_the_chosen_providers_fields_are_shown(self):
        arch = self.env['res.config.settings'].get_view(self.env.ref('ai_leadgen.res_config_settings_view_form').id)['arch']
        self.assertIn("ai_leadgen_ai_provider != 'deepseek'", arch)     # the DeepSeek block is hidden unless DeepSeek is chosen
        self.assertIn("ai_leadgen_ai_provider != 'gemini'", arch)       # and the Gemini block unless Gemini is chosen
        self.assertIn('action_ai_leadgen_test_ai', arch)

    def test_choosing_deepseek_fills_a_valid_model_and_thinking_level(self):
        with Form(self.env['res.config.settings']) as form:
            form.ai_leadgen_ai_provider = 'deepseek'
            self.assertIn(form.ai_leadgen_deepseek_model, ('deepseek-flash', 'deepseek-v4-pro'))
            self.assertEqual(form.ai_leadgen_deepseek_thinking, 'off')

    def test_the_choices_are_saved_and_reach_the_engine_environment(self):
        params = self.env['ir.config_parameter'].sudo()
        settings = self.env['res.config.settings'].create({
            'ai_leadgen_ai_provider': 'deepseek', 'ai_leadgen_deepseek_model': 'deepseek-v4-pro', 'ai_leadgen_deepseek_thinking': 'high',
            'ai_leadgen_deepseek_api_key': 'sk-unit-test', 'ai_leadgen_deepseek_daily_budget_usd': 0.25})
        settings.execute()
        self.assertEqual(params.get_param('ai_leadgen.ai_provider'), 'deepseek')
        env = self.env['leadgen.source']._engine_env()
        self.assertEqual((env['AI_PROVIDER'], env['DEEPSEEK_MODEL'], env['DEEPSEEK_THINKING'], env['DEEPSEEK_API_KEY'], env['DEEPSEEK_DAILY_BUDGET_USD']),
                         ('deepseek', 'deepseek-v4-pro', 'high', 'sk-unit-test', '0.25'))
        self.env['res.config.settings'].create({'ai_leadgen_ai_provider': 'gemini', 'ai_leadgen_gemini_model': 'gemini-2.5-flash'}).execute()   # switching back
        env = self.env['leadgen.source']._engine_env()
        self.assertEqual((env['AI_PROVIDER'], env['GEMINI_MODEL']), ('gemini', 'gemini-2.5-flash'))

    def test_the_test_button_reports_every_model(self):
        answer = {'provider': 'deepseek', 'results': [
            {'provider': 'deepseek', 'model': 'deepseek-flash', 'paid': True, 'selected': True, 'ok': True, 'seconds': 0.9, 'tokens': 88, 'cost_usd': 0.00002, 'error': ''},
            {'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'paid': True, 'selected': False, 'ok': False, 'seconds': None, 'tokens': None, 'cost_usd': None, 'error': 'balance is empty'}]}
        settings = self.env['res.config.settings'].create({'ai_leadgen_ai_provider': 'deepseek'})
        with patch(ENGINE, return_value=(0, json.dumps(answer), '')) as engine:
            action = settings.action_ai_leadgen_test_ai()
        self.assertEqual(engine.call_args[0][0], ['check-ai', '--provider', 'deepseek', '--json'])
        message = action['params']['message']
        self.assertIn('> deepseek-flash: OK', message)
        self.assertIn('deepseek-v4-pro: FAILED', message)
        self.assertEqual(action['params']['type'], 'warning')            # one model failed: the user is told, not shown a green tick
        with patch(ENGINE, return_value=(1, '', 'boom')), self.assertRaises(UserError):
            settings.action_ai_leadgen_test_ai()
