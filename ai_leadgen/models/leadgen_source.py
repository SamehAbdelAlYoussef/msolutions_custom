import json
import logging
import os
import re
import signal
import subprocess
from datetime import datetime, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

GROUP_URL = re.compile(r'^https?://(?:www\.|m\.|web\.|mbasic\.)?facebook\.com/groups/([\w.\-]+)', re.I)
NOT_GROUPS = {'joins', 'joined', 'feed', 'discover', 'create', 'notifications', 'your_groups'}
RESOLVE_TIMEOUT = 300  # the engine waits its own 3-7 s page pacing, then loads one search page
SYNC_TIMEOUT = 60
# Public Odoo communities on Facebook, in the order they should be read (an Egypt/Arab-first ranking, easy to reorder by drag and drop).
DEFAULT_GROUPS = [
    ("odoo Egypt - اودو مصر", "404184418132511"), ("Odoo ERP Egypt Group", "1926381244324092"), ("اودوو العرب Odoo Arabs", "221023788497762"),
    ("Arabian odoo Workshop", "odoo.in.arabic"), ("odoo society - مجتمع أودو", "odoosociety"), ("Odoo Developers", "odoodevelopers"),
    ("Odoo Mates", "odoomatesofficial"), ("Odoo Libya ( اودو ليبيا )", "1234914417070753"), ("Odoo Implementation Australia", "odoo.au"),
    ("OdooERP-Ethiopia", "153412428368117"),
]


def normalize_group_url(url):
    """Canonical https://www.facebook.com/groups/<id-or-slug>, or '' when the text is not a Facebook group link."""
    match = GROUP_URL.match((url or '').strip())
    return f"https://www.facebook.com/groups/{match.group(1)}" if match and match.group(1) not in NOT_GROUPS else ''


class LeadgenSource(models.Model):
    _name = 'leadgen.source'
    _description = "AI Lead Gen Target Group"
    _order = 'sequence, id'

    sequence = fields.Integer(
        default=10, help="Drag the handle to reorder. The top of the list is polled most often: ranks 1-3 about 3x, ranks 4-6 about 2x.")
    name = fields.Char("Group Name", required=True)
    url = fields.Char("Group URL", copy=False)
    platform = fields.Selection([('facebook', "Facebook")], default='facebook', required=True)
    enabled = fields.Boolean("Active", default=True, help="Only active, verified groups are polled by the engine.")
    verified = fields.Boolean(
        readonly=True, copy=False,
        help="The URL is a direct group link: pasted by you, found by Search / Resolve URL with an exact name match, or confirmed by you.")
    member_count = fields.Char(readonly=True, copy=False)
    resolve_note = fields.Text("Search result", readonly=True, copy=False)
    source = fields.Selection([('manual', "Added here"), ('engine', "Found by the engine")], default='manual', readonly=True)
    status = fields.Selection(
        [('polled', "Being polled"), ('off', "Switched off"), ('needs_url', "Needs URL"), ('needs_confirm', "Confirm match")],
        compute='_compute_status', store=True)
    weight = fields.Integer("Poll weight", readonly=True, copy=False, help="How many times more often than an unranked group the engine reads this one.")
    last_visited = fields.Datetime(readonly=True, copy=False)
    engine_synced = fields.Boolean(readonly=True, copy=False, help="The engine's queue holds this group's current settings.")

    _url_uniq = models.Constraint('unique(url)', "This group is already in the list.")

    # -------------------------------------------------------------------------
    # COMPUTE / CONSTRAINTS
    # -------------------------------------------------------------------------

    @api.depends('enabled', 'verified', 'url')
    def _compute_status(self):
        for group in self:
            if not group.enabled:
                group.status = 'off'
            elif not group.url:
                group.status = 'needs_url'
            elif not group.verified:
                group.status = 'needs_confirm'
            else:
                group.status = 'polled'

    @api.model
    def _prepare_values(self, vals):
        """Normalise a typed URL. A valid group link typed or pasted by the user counts as verified."""
        vals = dict(vals)
        if 'url' in vals:
            raw = (vals['url'] or '').strip()
            if raw:
                url = normalize_group_url(raw)
                if not url:
                    raise ValidationError(self.env._("%s is not a Facebook group link (https://www.facebook.com/groups/...).", raw))
                vals.update(url=url, engine_synced=False)
                vals.setdefault('verified', True)  # a link the user typed or pasted is a direct group link
            else:
                vals.update(url=False, verified=False, engine_synced=False)
        return vals

    # -------------------------------------------------------------------------
    # CRUD: every change reaches the engine's queue
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._prepare_values(vals) for vals in vals_list]
        groups = super().create(vals_list)
        groups._sync_to_engine()
        return groups

    def write(self, vals):
        vals = self._prepare_values(vals)
        result = super().write(vals)
        if {'name', 'url', 'enabled', 'verified', 'sequence'} & set(vals):
            self._sync_to_engine()
        return result

    def unlink(self):
        removed = [self._payload(group, enabled=False) for group in self if group.url]
        result = super().unlink()
        self._sync_to_engine(extra=removed)  # a deleted group must stop being polled
        return result

    # -------------------------------------------------------------------------
    # ENGINE BRIDGE
    # -------------------------------------------------------------------------

    @api.model
    def _payload(self, group, enabled=None):
        return {
            'url': group.url or '', 'name': group.name, 'sequence': group.sequence,
            'enabled': group.enabled if enabled is None else enabled, 'verified': group.verified,
        }

    @api.model
    def _engine_env(self):
        from .ai_leadgen_run import ENGINE_ENV_PARAMS

        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}
        params = self.env['ir.config_parameter'].sudo()
        for param, variable in ENGINE_ENV_PARAMS.items():  # what was saved in the settings reaches every engine command (secrets only travel in the environment)
            value = params.get_param(param)
            if value:
                env[variable] = value
        return env

    @api.model
    def _run_engine(self, args, payload=None, timeout=SYNC_TIMEOUT, uncapped=False):
        """Run `main.py <args>` and return (exit code, stdout, stderr).

        The command runs in its own process group. On a timeout the WHOLE group is killed, so a browser
        the engine started can never outlive the request."""
        engine_root, python = self.env['ai.leadgen.run']._get_engine_paths()
        command = [str(python), 'main.py', *args]
        if uncapped:  # Chromium needs the address space Odoo's own limit would otherwise hand down
            command = self.env['ai.leadgen.run']._without_address_space_cap(command)
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command, cwd=engine_root, env=self._engine_env(), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(json.dumps(payload) if payload is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise UserError(self.env._("The engine did not answer within %s seconds and was stopped.", timeout)) from None
        return process.returncode, stdout, stderr

    def _sync_to_engine(self, extra=None):
        """Odoo -> engine: send the whole list (the order is the priority), then read back visit times and weights.

        Never blocks the user's edit: if the engine cannot be reached the rows stay unsynced and the cron retries."""
        if self.env.context.get('install_mode') or self.env.context.get('leadgen_no_sync'):
            return
        if not self.env['ai.leadgen.run']._engine_available():  # no engine here: the list is just a list, nothing to tell
            return
        everyone = self.sudo().search([])
        payload = (extra or []) + [self._payload(group) for group in everyone]
        try:
            code, stdout, stderr = self._run_engine(['sync-groups'], payload)
            if code:
                raise UserError(stderr.strip()[-300:] or f"exit code {code}")
            queue = json.loads(stdout)
        except (UserError, OSError, ValueError, subprocess.SubprocessError) as exc:
            _logger.warning("Cannot sync target groups to the engine: %s", exc)
            everyone.with_context(leadgen_no_sync=True).write({'engine_synced': False})
            return
        self.sudo()._apply_engine_queue(queue, everyone)

    @api.model
    def _apply_engine_queue(self, queue, groups=None):
        by_url = {row['url']: row for row in queue}
        for group in groups if groups is not None else self.sudo().search([]):
            row = by_url.get(group.url)
            values = {'engine_synced': bool(row)}
            if row:
                values.update(
                    weight=row.get('weight') or 1 if row.get('relevant') else 0,
                    last_visited=self._parse_time(row.get('last_visited_at')))
            group.with_context(leadgen_no_sync=True).write(values)

    @staticmethod
    def _parse_time(value):
        return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None) if value else False

    @api.model
    def _pull_from_engine(self, import_new=True):
        """Engine -> Odoo: refresh visit times and weights, and (only when asked) list engine-found relevant groups that Odoo does not know yet."""
        code, stdout, stderr = self._run_engine(['sync-groups'], [])
        if code:
            raise UserError(self.env._("The engine's group queue cannot be read: %s", stderr.strip()[-300:]))
        queue = json.loads(stdout)
        known = {g.url for g in self.sudo().search([])}
        last = self.sudo().search([], order='sequence desc', limit=1).sequence or 0
        new = []
        for row in queue:
            if import_new and row['url'] not in known and row.get('relevant'):
                last += 10
                new.append({
                    'name': row['name'], 'url': row['url'], 'sequence': last, 'enabled': True, 'source': 'engine'})
        created = self.sudo().with_context(leadgen_no_sync=True).create(new) if new else self.browse()
        created.with_context(leadgen_no_sync=True).write({'verified': True})
        self.sudo()._apply_engine_queue(queue)
        return len(created)

    @api.model
    def _cron_sync_engine(self):
        """Every 15 minutes: send pending changes to the engine and bring its visit times back."""
        if not self.env['ai.leadgen.run']._engine_available():
            return
        try:
            self._pull_from_engine(import_new=False)  # the list is the user's: the scheduled sync never adds groups by itself
        except (UserError, OSError, ValueError, subprocess.SubprocessError) as exc:
            _logger.warning("Target group sync skipped: %s", exc)
            return
        self.sudo().search([('engine_synced', '=', False)])._sync_to_engine()

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_search_facebook_group(self):
        """Search Facebook for this group by NAME with the engine's saved login: one safe page visit, then fill in the exact
        name, direct URL and member count. The engine opens and closes its own browser; a timeout kills it."""
        self.ensure_one()
        name = (self.name or '').strip()
        if not name:
            raise UserError(self.env._("Type the group name first."))
        code, stdout, stderr = self._run_engine(['resolve-group', '--name', name], timeout=RESOLVE_TIMEOUT, uncapped=True)
        try:
            result = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        except ValueError:
            result = {}
        if code or result.get('error') or 'matches' not in result:
            raise UserError(result.get('error') or stderr.strip()[-300:] or self.env._("The search failed (exit code %s).", code))
        matches = result['matches']
        if not matches:
            self.resolve_note = self.env._("Facebook found no group named \"%s\". Check the spelling or paste the group's link.", name)
            return self._notify(self.env._("No group found"), self.resolve_note, 'warning')
        self.resolve_note = '\n'.join(
            f"{i}. {m['name']} - {m['url']}" + (f" ({m['members']})" if m.get('members') else '')
            for i, m in enumerate(matches[:10], 1))
        exact = next((m for m in matches if self._same_name(m['name'], name)), None)
        best = exact or matches[0]
        if exact:
            self.with_context(leadgen_no_sync=True).write({'name': best['name']})
            self.write({'url': best['url'], 'member_count': best.get('members') or False})
            return self._notify(self.env._("Group resolved"), f"{best['name']}\n{best['url']}", 'success')
        # Not an exact match: show it and let the user decide. Nothing is polled until they confirm.
        self.with_context(leadgen_no_sync=True).write({'url': best['url'], 'verified': False, 'member_count': best.get('members') or False})
        return self._notify(
            self.env._("Closest match found"),
            self.env._("Facebook returned \"%s\", not exactly what you typed. Check it, then press Confirm.", best['name']), 'warning')

    def action_confirm(self):
        """The user checked the resolved group: from now on it is polled (if Active)."""
        for group in self:
            if not group.url:
                raise UserError(self.env._("\"%s\" has no URL yet: use Search / Resolve URL or paste the link.", group.name))
        self.write({'verified': True})

    @api.model
    def action_import_from_engine(self):
        count = self._pull_from_engine()
        return self._notify(
            self.env._("Engine queue read"), self.env._("%s group(s) found by the engine were added to the list.", count), 'info')

    @staticmethod
    def _same_name(found, typed):
        def key(text):
            return re.sub(r'[\W_]+', '', text or '', flags=re.UNICODE).casefold()
        return key(found) == key(typed)

    def _notify(self, title, message, kind):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': kind, 'sticky': kind == 'warning', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    @api.model
    def _seed_default_groups(self):
        """One-time tenant setup (`odoo shell -d <tenant>`): the Odoo groups the account belongs to, best first. Idempotent. Deliberately NOT module data:
        this module is installable by every tenant of a SaaS and must not bring one customer's list with it."""
        created = 0
        for sequence, (name, group_id) in enumerate(DEFAULT_GROUPS, start=1):
            url = f"https://www.facebook.com/groups/{group_id}"
            if not self.sudo().with_context(active_test=False).search_count([('url', '=', url)]):
                self.sudo().with_context(leadgen_no_sync=True).create({
                    'name': name, 'url': url, 'sequence': sequence * 10, 'verified': True, 'source': 'engine'})
                created += 1
        return created
