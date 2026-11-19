import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psutil

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiLeadgenSession(models.Model):
    _name = 'ai.leadgen.session'
    _description = "AI Lead Gen Platform Authorization"
    _rec_name = 'platform'
    _order = 'platform'

    platform = fields.Selection(
        [('facebook', "Facebook"), ('linkedin', "LinkedIn")], required=True, readonly=True,
    )
    state = fields.Selection(
        [
            ('connected', "Connected"),
            ('expiring', "Expiring soon"),
            ('paused', "Paused after a platform warning"),
            ('missing', "Not authorized"),
            ('logging_in', "Waiting for your login"),
            ('unknown', "Unknown"),
        ],
        compute='_compute_status',
    )
    age_days = fields.Float(compute='_compute_status', digits=(6, 1))
    paused_until = fields.Datetime(compute='_compute_status', help="The platform showed a warning: the engine stays away until then.")
    needed = fields.Boolean(compute='_compute_status', help="Your configured groups are on this platform.")
    login_pid = fields.Integer(copy=False, groups='base.group_system')

    _platform_uniq = models.Constraint('unique(platform)', "There is one authorization record per platform.")

    # -------------------------------------------------------------------------
    # COMPUTE
    # -------------------------------------------------------------------------

    @api.depends_context('uid')
    def _compute_status(self):
        report = self._read_engine_status()
        for session in self:
            row = report.get(session.platform)
            session.needed = bool(row and row.get('needed'))
            session.age_days = (row or {}).get('age_days') or 0.0
            session.paused_until = self._parse_until((row or {}).get('paused_until'))
            if session._login_process():
                session.state = 'logging_in'
            elif session.paused_until:
                session.state = 'paused'
            else:
                session.state = row['state'] if row else 'unknown'

    @api.model
    def _read_engine_status(self):
        """{platform: {state, age_days, needed}} from the engine (`main.py sessions --json`); {} if it cannot be read."""
        if not self.env['ai.leadgen.run']._engine_available():
            return {}
        try:
            engine_root, python = self.env['ai.leadgen.run']._get_engine_paths()
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(python), 'main.py', 'sessions', '--json'], cwd=engine_root, env=self._base_env(),
                capture_output=True, text=True, timeout=30, check=True,
            )
            return {row['platform']: row for row in json.loads(done.stdout)}
        except (UserError, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            _logger.warning("Cannot read the engine's login sessions: %s", exc)
            return {}

    @staticmethod
    def _parse_until(value):
        if not value:
            return False
        return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None)

    @api.model
    def _paused_until(self):
        """The latest time until which a platform warning pauses the engine, or False."""
        untils = [self._parse_until(row.get('paused_until')) for row in self._read_engine_status().values()]
        return max((u for u in untils if u), default=False)

    @api.model
    def _base_env(self):
        return {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}

    @api.model
    def _missing_platforms(self):
        """Labels of the platforms your groups need that have no saved session yet."""
        labels = dict(self._fields['platform'].selection)
        return [labels[p] for p, row in self._read_engine_status().items() if row.get('needed') and row.get('state') == 'missing']

    def _login_process(self):
        """The login window process while it is still open, else None."""
        self.ensure_one()
        pid = self.sudo().login_pid
        if not pid:
            return None
        try:
            process = psutil.Process(pid)
            cmdline = process.cmdline()
            if process.status() != psutil.STATUS_ZOMBIE and 'login' in cmdline and self.platform in cmdline:
                return process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_authorize(self):
        """Open the platform's login page in a visible browser window; the user logs in and the session is saved."""
        self.ensure_one()
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise UserError(self.env._(
                "This server has no screen to open a login window on. Run the login on a computer with a screen:\n"
                "python main.py login --platform %(platform)s\n"
                "then copy data/sessions/%(platform)s.json and data/sessions/%(platform)s.meta.json to the server.",
                platform=self.platform,
            ))
        if self._login_process():
            raise UserError(self.env._("A login window for this platform is already open."))
        run = self.env['ai.leadgen.run']
        engine_root, python = run._get_engine_paths()
        log_dir = Path(tools.config['data_dir']) / 'ai_leadgen' / self.env.cr.dbname
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f'login_{self.platform}.log', 'wb') as log_file:
            process = subprocess.Popen(  # noqa: S603 - fixed argv built from validated paths, no shell
                run._without_address_space_cap([str(python), 'main.py', 'login', '--platform', self.platform]),
                cwd=engine_root, env=self._base_env(), stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.sudo().login_pid = process.pid
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._("Log in to %s", dict(self._fields['platform'].selection)[self.platform]),
                'message': self.env._(
                    "A browser window opened on this computer. Log in yourself (2FA included). "
                    "The session is saved when you finish; then press Refresh."),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_refresh(self):
        return {'type': 'ir.actions.client', 'tag': 'reload'}
