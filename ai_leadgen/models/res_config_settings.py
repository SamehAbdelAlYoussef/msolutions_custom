from odoo import api, fields, models
from odoo.exceptions import UserError

AI_PROVIDERS = [('gemini', "Google Gemini (free tier)"), ('deepseek', "DeepSeek (paid per token)")]
GEMINI_MODELS = [
    ('gemini-3.5-flash-lite', "Gemini 3.5 Flash Lite: 500 free requests a day (recommended)"),
    ('gemini-2.5-flash', "Gemini 2.5 Flash: 20 free requests a day"),
]
DEEPSEEK_MODELS = [
    ('deepseek-flash', "DeepSeek V4.1 Flash: cheap, about $0.15 / $0.60 per million tokens (recommended)"),
    ('deepseek-v4-pro', "DeepSeek V4 Pro: stronger, about 4 times the price"),
]
DEEPSEEK_THINKING = [
    ('off', "Off: cheapest, enough for sorting requests (recommended)"),
    ('low', "Low"),
    ('high', "High: deeper analysis, many more tokens"),
    ('max', "Max: deep research, the most tokens"),
]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_leadgen_engine_path = fields.Char(
        string="Engine Folder",
        config_parameter='ai_leadgen.engine_path',
        help="Folder that contains the engine's main.py. Empty: the folder this module ships in.",
    )
    ai_leadgen_python_path = fields.Char(
        string="Engine Python",
        config_parameter='ai_leadgen.python_path',
        help="Python interpreter of the engine's virtualenv. Empty: <engine folder>/.venv/bin/python.",
    )
    ai_leadgen_ai_provider = fields.Selection(
        AI_PROVIDERS, string="AI Provider", config_parameter='ai_leadgen.ai_provider', default='gemini',
        help="Which provider judges the requests. Gemini has a free tier. DeepSeek is paid per token and is only used when you choose it here.",
    )
    ai_leadgen_gemini_api_key = fields.Char(string="Gemini API Key", config_parameter='ai_leadgen.gemini_api_key')
    ai_leadgen_gemini_model = fields.Selection(
        GEMINI_MODELS, string="Gemini Model", config_parameter='ai_leadgen.gemini_model', default='gemini-3.5-flash-lite',
    )
    ai_leadgen_deepseek_api_key = fields.Char(string="DeepSeek API Key", config_parameter='ai_leadgen.deepseek_api_key')
    ai_leadgen_deepseek_model = fields.Selection(
        DEEPSEEK_MODELS, string="DeepSeek Model", config_parameter='ai_leadgen.deepseek_model', default='deepseek-flash',
    )
    ai_leadgen_deepseek_thinking = fields.Selection(
        DEEPSEEK_THINKING, string="Thinking", config_parameter='ai_leadgen.deepseek_thinking', default='off',
        help="DeepSeek V4 reasons before answering and every reasoning token is billed. Off is the cheapest and is enough for sorting requests.",
    )
    ai_leadgen_deepseek_daily_budget_usd = fields.Float(
        string="Daily Budget (USD)", config_parameter='ai_leadgen.deepseek_daily_budget_usd', default=0.10,
        help="The engine stops using DeepSeek for the rest of the day when its estimated spend reaches this amount.",
    )
    ai_leadgen_enrich_min_score = fields.Integer(
        string="Minimum Score", config_parameter='ai_leadgen.enrich_min_score', default=7,
    )
    ai_leadgen_proxy_url = fields.Char(string="Proxy URL", config_parameter='ai_leadgen.proxy_url')
    ai_leadgen_google_places_api_key = fields.Char(
        string="Google Places API Key", config_parameter='ai_leadgen.google_places_api_key',
        help="Optional. Phone numbers and websites of businesses from the official Google Places API.",
    )

    @api.onchange('ai_leadgen_ai_provider')
    def _onchange_ai_leadgen_ai_provider(self):
        """Choosing a provider shows its models; make sure a valid model is selected for it."""
        if self.ai_leadgen_ai_provider == 'deepseek':
            self.ai_leadgen_deepseek_model = self.ai_leadgen_deepseek_model or 'deepseek-flash'
            self.ai_leadgen_deepseek_thinking = self.ai_leadgen_deepseek_thinking or 'off'
        else:
            self.ai_leadgen_gemini_model = self.ai_leadgen_gemini_model or 'gemini-3.5-flash-lite'

    def action_ai_leadgen_test_ai(self):
        """Settings are saved first (Odoo does it for object buttons), then ONE tiny real call per model of the chosen provider."""
        import json

        provider = self.ai_leadgen_ai_provider or 'gemini'
        code, stdout, stderr = self.env['leadgen.source']._run_engine(['check-ai', '--provider', provider, '--json'], timeout=120)
        if code:
            raise UserError(self.env._("The AI test could not run: %s", (stderr or stdout).strip()[-300:]))
        results = json.loads(stdout)['results']
        lines = []
        for r in results:
            mark = "OK" if r['ok'] else "FAILED"
            extra = f", {r['tokens']} tokens, ${r['cost_usd']:.5f}" if r.get('tokens') else ""
            lines.append(f"{'> ' if r['selected'] else ''}{r['model']}: {mark} in {r['seconds']}s{extra}{'' if r['ok'] else ' - ' + r['error']}")
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._("AI connection test (%s)", provider), 'message': "\n".join(lines),
                       'type': 'success' if all(r['ok'] for r in results) else 'warning', 'sticky': True},
        }
