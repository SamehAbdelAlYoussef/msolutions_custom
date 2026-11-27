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

DEFAULT_STYLE_GUIDE = """- ادخل في الموضوع من أول كلمة: قول الحل أو الخطوة العملية مباشرة.
- اكتب بصيغة المتكلم (أنا)، كمتخصص بينفذ الشغل بنفسه.
- عربي بسيط وواضح قريب من كلام السوق، من غير تكلف.
- من سطرين لأربعة سطور قصيرة، من غير عناوين ولا نقط ولا إيموجي."""
DEFAULT_CONTACT_LINES = """للتفاصيل كلمني واتساب: {whatsapp}
لو حابب نتكلم فيها: {whatsapp}
تقدر تبعتلي التفاصيل هنا: {whatsapp}
ابعتلي على الواتساب ونشوفها سوا: {whatsapp}"""
TEXT_PARAMS = [  # (settings field, parameter, default) - multi-line texts cannot use config_parameter
    ('ai_leadgen_comment_style_guide', 'ai_leadgen.comment_style_guide', DEFAULT_STYLE_GUIDE),
    ('ai_leadgen_comment_contact_lines', 'ai_leadgen.comment_contact_lines', DEFAULT_CONTACT_LINES),
    ('ai_leadgen_comment_examples', 'ai_leadgen.comment_examples', ''),
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

    # --- replies on Facebook posts ------------------------------------------------
    ai_leadgen_auto_comment_enabled = fields.Boolean(
        string="Reply Automatically", config_parameter='ai_leadgen.auto_comment_enabled',
        help="Every new request from a Facebook group gets its reply planned 10 to 20 minutes after the post was read. "
             "Off: replies are only written, and you plan each one from the lead.")
    ai_leadgen_max_comments_per_day = fields.Integer(
        string="Replies per Day (maximum)", config_parameter='ai_leadgen.max_comments_per_day', default=10,
        help="The engine never posts more than this in 24 hours, and posts fewer whenever its risk score rises.")
    ai_leadgen_whatsapp_link = fields.Char(string="WhatsApp Link", config_parameter='ai_leadgen.whatsapp_link')
    ai_leadgen_website_link = fields.Char(string="Website", config_parameter='ai_leadgen.website_link')
    ai_leadgen_comment_include_whatsapp = fields.Boolean(
        string="Put the WhatsApp Link in Replies", config_parameter='ai_leadgen.comment_include_whatsapp', default=True)
    ai_leadgen_comment_include_website = fields.Boolean(
        string="Put the Website in Replies", config_parameter='ai_leadgen.comment_include_website')
    ai_leadgen_auto_delete_days = fields.Integer(
        string="Delete Untouched Leads After (days)", config_parameter='ai_leadgen.auto_delete_days', default=3,
        help="Generated leads still in New or in the commented stage are deleted after this many days. 0 keeps them forever.")
    ai_leadgen_comment_style_guide = fields.Text(
        string="Reply Style",
        help="How a reply must read: tone, length, what to say first, what never to say. The AI reads this before writing every reply.")
    ai_leadgen_comment_contact_lines = fields.Text(
        string="Closing Lines",
        help="One closing line per row. {whatsapp} and {website} are replaced by the links above. "
             "The engine picks a different line each time, so two replies never end the same way.")
    ai_leadgen_comment_examples = fields.Text(
        string="Example Replies", help="Replies you like, separated by an empty line. The AI imitates their tone, never their words.")

    @api.model
    def get_values(self):
        res = super().get_values()
        get = self.env['ir.config_parameter'].sudo().get_param
        for name, key, default in TEXT_PARAMS:
            res[name] = get(key) or default
        return res

    def set_values(self):
        super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        for name, key, _default in TEXT_PARAMS:
            params.set_param(key, (self[name] or '').strip())

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
