import json
import logging
import os
import signal
import subprocess
from datetime import timedelta
from pathlib import Path

import psutil

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# models/ -> ai_leadgen/ -> odoo_addons/ -> engine root
DEFAULT_ENGINE_ROOT = Path(__file__).resolve().parents[3]
KEY_VALIDITY = timedelta(hours=3)
LOG_TAIL_CHARS = 30000
ENGINE_ENV_PARAMS = {
    # config parameter -> engine environment variable (only passed when set)
    'ai_leadgen.gemini_api_key': 'GEMINI_API_KEY',
    'ai_leadgen.gemini_model': 'GEMINI_MODEL',
    'ai_leadgen.ai_provider': 'AI_PROVIDER',
    'ai_leadgen.deepseek_api_key': 'DEEPSEEK_API_KEY',
    'ai_leadgen.deepseek_model': 'DEEPSEEK_MODEL',
    'ai_leadgen.deepseek_thinking': 'DEEPSEEK_THINKING',
    'ai_leadgen.deepseek_daily_budget_usd': 'DEEPSEEK_DAILY_BUDGET_USD',
    'ai_leadgen.enrich_min_score': 'ENRICH_MIN_SCORE',
    'ai_leadgen.proxy_url': 'PROXY_URL',
    'ai_leadgen.google_places_api_key': 'GOOGLE_PLACES_API_KEY',
}


class AiLeadgenRun(models.Model):
    _name = 'ai.leadgen.run'
    _description = "AI Lead Gen Run"
    _order = 'id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: self.env._("Run %s", fields.Datetime.to_string(fields.Datetime.now())),
    )
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('running', "Running"),
            ('done', "Done"),
            ('failed', "Failed"),
            ('cancelled', "Cancelled"),
        ],
        default='draft', required=True, copy=False, readonly=True,
    )
    source = fields.Selection(
        [
            ('all', "All enabled sources"),
            ('community', "Facebook groups (the ones listed in Target Groups)"),
            ('telegram', "Telegram (authorized communities and direct submissions)"),
            ('maps', "Businesses on maps"),
        ],
        default='all', required=True,
    )
    dry_run = fields.Boolean(string="Dry Run", help="Scrape and analyse, but create nothing in CRM.")
    limit = fields.Integer(default=30)
    hours = fields.Integer(string="Lookback (hours)", default=24)
    started = fields.Datetime(copy=False, readonly=True)
    finished = fields.Datetime(copy=False, readonly=True)
    # The next three are internal and only ever touched by the methods below (through sudo).
    pid = fields.Integer(copy=False, groups='base.group_system')
    summary_path = fields.Char(copy=False, groups='base.group_system')
    log_path = fields.Char(copy=False, groups='base.group_system')
    log = fields.Text(string="Engine Log", copy=False, readonly=True)
    summary = fields.Json(copy=False, readonly=True)
    line_ids = fields.One2many('ai.leadgen.run.line', 'run_id', string="Results", copy=False)
    pushed_count = fields.Integer(compute='_compute_counts')
    discarded_count = fields.Integer(compute='_compute_counts')
    deferred_count = fields.Integer(compute='_compute_counts')
    error_count = fields.Integer(compute='_compute_counts')
    session_warning = fields.Char(compute='_compute_session_warning')

    @api.depends('line_ids.outcome')
    def _compute_counts(self):
        for run in self:
            outcomes = run.line_ids.mapped('outcome')
            run.pushed_count = outcomes.count('pushed')
            run.discarded_count = outcomes.count('discarded')
            run.deferred_count = outcomes.count('deferred')
            run.error_count = outcomes.count('error')

    @api.depends('source', 'state')
    def _compute_session_warning(self):
        draft_community = self.filtered(lambda r: r.state == 'draft' and r.source in ('community', 'all'))
        missing = self.env['ai.leadgen.session']._missing_platforms() if draft_community else []
        paused = self.env['ai.leadgen.session']._paused_until() if draft_community else False
        for run in self:
            if run in draft_community and paused:
                run.session_warning = self.env._("Paused until %s (UTC): a platform showed a warning and the engine is staying away.", paused)
                continue
            run.session_warning = (
                self.env._("Authorize %s first (AI Lead Gen > Authorizations): the community source cannot read those groups yet.",
                           ", ".join(missing))
                if run in draft_community and missing else False
            )

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    @api.model
    def action_deep_sync(self):
        """One click: read the configured Facebook / LinkedIn groups and resolve the authors' contacts (the Deep Sync Run)."""
        paused = self.env['ai.leadgen.session']._paused_until()
        if paused:
            raise UserError(self.env._(
                "A platform showed a warning, so the engine is paused until %s (UTC) to protect the account. Nothing was started.", paused))
        missing = self.env['ai.leadgen.session']._missing_platforms()
        if missing:
            raise UserError(self.env._("Authorize %s first: open AI Lead Gen > Authorizations and press Authorize.", ", ".join(missing)))
        run = self.create({'source': 'community', 'hours': 48, 'limit': 30})
        run.action_start()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.leadgen.run',
            'res_id': run.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_start(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._("Only a draft run can be started."))
        engine_root, python = self._get_engine_paths()

        runs_dir = Path(tools.config['data_dir']) / 'ai_leadgen' / self.env.cr.dbname
        runs_dir.mkdir(parents=True, exist_ok=True)
        summary_path = runs_dir / f'run_{self.id}.json'
        log_path = runs_dir / f'run_{self.id}.log'
        summary_path.unlink(missing_ok=True)

        command = [
            str(python), 'main.py', 'run',
            '--summary-file', str(summary_path),
            '--limit', str(self.limit),
            '--hours', str(self.hours),
        ]
        if self.source != 'all':
            command += ['--platform', self.source]
        if self.dry_run:
            command.append('--dry-run')

        with open(log_path, 'wb') as log_file:
            process = subprocess.Popen(  # noqa: S603 - fixed argv built from validated paths, no user data in a shell string
                self._without_address_space_cap(command),
                cwd=engine_root,
                env=self._get_engine_env(),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group, so Stop can end the engine and its browser
            )
        self.sudo().write({'pid': process.pid, 'summary_path': str(summary_path), 'log_path': str(log_path)})
        self.write({'state': 'running', 'started': fields.Datetime.now(), 'finished': False})
        return True

    def action_refresh(self):
        for run in self.filtered(lambda r: r.state == 'running'):
            run._refresh_running()
        return True

    def action_stop(self):
        self.ensure_one()
        if self.state != 'running':
            raise UserError(self.env._("Only a running run can be stopped."))
        process = self._engine_process()
        if process:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass  # it ended by itself in the meantime
        self._finish('cancelled')
        return True

    # -------------------------------------------------------------------------
    # CRON
    # -------------------------------------------------------------------------

    @api.model
    def _cron_refresh_running(self):
        self.search([('state', '=', 'running')]).action_refresh()

    @api.model
    def _cron_start_scheduled_run(self):
        self.create({}).action_start()

    # -------------------------------------------------------------------------
    # ENGINE
    # -------------------------------------------------------------------------

    def _engine_available(self):
        """True when the engine is installed where this Odoo runs. A SaaS server that only ships the module (the engine lives elsewhere)
        answers False, and every engine-facing job then stays silent instead of logging an error every few minutes."""
        try:
            self._get_engine_paths()
            return True
        except UserError:
            return False

    def _get_engine_paths(self):
        params = self.env['ir.config_parameter'].sudo()
        engine_root = Path(params.get_param('ai_leadgen.engine_path') or DEFAULT_ENGINE_ROOT)
        python = Path(params.get_param('ai_leadgen.python_path') or engine_root / '.venv' / 'bin' / 'python')
        if not (engine_root / 'main.py').is_file():
            raise UserError(self.env._("Engine not found: %s/main.py does not exist. Set the engine folder in Settings.", engine_root))
        if not python.is_file():
            raise UserError(self.env._("Engine Python not found: %s. Set it in Settings.", python))
        return engine_root, python

    @staticmethod
    def _without_address_space_cap(command):
        """Lift the memory cap the engine would inherit from Odoo.

        Odoo caps its own address space (limit_memory_hard, 2.5 GB by default) and every child process
        inherits it. The engine drives a headless Chromium, which reserves far more virtual memory than
        that and dies at startup. The soft limit is raised back to the hard limit in the child only; the
        command is passed as positional arguments, so nothing is interpolated into the shell string.
        """
        return ['/bin/bash', '-c', 'ulimit -S -v "$(ulimit -H -v)"; exec "$@"', 'ai_leadgen', *command]

    def _get_engine_env(self):
        """Environment for the engine process. Secrets only travel in it, they are never stored or logged."""
        params = self.env['ir.config_parameter'].sudo()
        base_url = params.get_param('ai_leadgen.odoo_url') or f"http://127.0.0.1:{tools.config['http_port']}"
        # A short-lived key for the current user: the engine creates leads with this user's access rights.
        api_key = self.env['res.users.apikeys'].sudo()._generate(
            None, "AI Lead Gen engine", fields.Datetime.now() + KEY_VALIDITY,
        )
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}
        env.update({
            'ODOO_URL': base_url,
            'ODOO_DB': self.env.cr.dbname,
            'ODOO_USER': self.env.user.login,
            'ODOO_API_KEY': api_key,
        })
        for param, variable in ENGINE_ENV_PARAMS.items():
            value = params.get_param(param)
            if value:
                env[variable] = value
        return env

    def _engine_process(self):
        """The live engine process of this run, or None. Never matches a recycled pid of another program."""
        self.ensure_one()
        run = self.sudo()
        if not run.pid:
            return None
        try:
            os.waitpid(run.pid, os.WNOHANG)  # reap our own finished child so it is not left as a zombie
        except OSError:
            pass  # not our child (another worker started it) or already reaped
        try:
            process = psutil.Process(run.pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            if run.summary_path and run.summary_path not in process.cmdline():
                return None
            return process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def _refresh_running(self):
        self.ensure_one()
        summary_path = Path(self.sudo().summary_path or '')
        if summary_path.is_file():
            self._load_summary(summary_path)
        elif not self._engine_process():
            self._finish('failed')  # the process ended without writing a result: see the log

    def _load_summary(self, summary_path):
        data = json.loads(summary_path.read_text(encoding='utf-8'))
        if data.get('error'):
            self._finish('failed')
            return
        lead_ids = {row['odoo_id'] for row in data.get('rows', []) if row.get('odoo_id')}
        existing_leads = set(self.env['crm.lead'].with_context(active_test=False).browse(lead_ids).exists().ids)
        self.line_ids.sudo().unlink()
        self.env['ai.leadgen.run.line'].sudo().create([{
            'run_id': self.id,
            'source': row.get('platform'),
            'title': row.get('title'),
            'score': row.get('score') or 0,
            'outcome': row.get('outcome'),
            'reason': row.get('reason'),
            'contacts': row.get('contacts'),
            'lead_id': row['odoo_id'] if row.get('odoo_id') in existing_leads else False,
        } for row in data.get('rows', [])])
        self.summary = {key: data.get(key) for key in ('counters', 'sources', 'gate_pass_rate', 'dry_run')}
        for source, state in (data.get('sources') or {}).items():
            if str(state).startswith('blocked'):
                _logger.error("AI LEAD GEN WARNING: %s was blocked or showed a platform warning (%s). The engine is paused "
                              "for 12-24 hours to protect the account. Run %s.", source, state, self.id)
        self._finish('done')

    def _finish(self, state):
        self.ensure_one()
        log_path = Path(self.sudo().log_path or '')
        log = ''
        if log_path.is_file():
            log = log_path.read_text(encoding='utf-8', errors='replace')[-LOG_TAIL_CHARS:]
        self.write({'state': state, 'finished': fields.Datetime.now(), 'log': log})
