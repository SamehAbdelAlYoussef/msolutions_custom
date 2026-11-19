import json
import logging
import subprocess
from datetime import datetime, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CANDIDATE_STATES = [
    ('new', "Pending"), ('deferred', "Waiting to retry"), ('pushed', "Lead created"), ('rejected', "Rejected"),
    ('duplicate', "Duplicate"), ('error', "Failed"), ('stale', "Too old"),
]
INTENT_LEVELS = [
    ('explicit_buyer', "Explicit buyer"), ('strong_intent', "Strong purchase intent"), ('general_prospect', "General prospect"),
    ('unqualified', "Unqualified"), ('advertisement', "Advertisement / spam"),
]
INTENT_CATEGORIES = [
    ('odoo_buyer', "Explicit Odoo Buyer"), ('erp_buyer', "ERP Buyer"), ('software_dev_buyer', "Software Development Buyer"),
    ('ai_automation_buyer', "AI Automation Buyer"), ('maintenance_customization_buyer', "Maintenance / Customization Buyer"),
    ('unqualified', "Unqualified Candidate"),
]
SOURCE_LABELS = {'community': "Facebook groups", 'maps': "Businesses on maps (OpenStreetMap)", 'telegram': "Telegram"}


def _dt(value):
    """Engine timestamps (ISO, naive or with an offset) -> naive UTC datetime for Odoo, or False."""
    if not value:
        return False
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class AiLeadgenSourceHealth(models.Model):
    _name = 'ai.leadgen.source.health'
    _description = "AI Lead Gen Source Health"
    _order = 'name'

    name = fields.Char("Source key", required=True, readonly=True)
    label = fields.Char(compute='_compute_label')
    enabled = fields.Boolean(readonly=True)
    configured = fields.Integer("Configured units", readonly=True, help="Feeds, pages, chats or searches set up for this source.")
    permitted = fields.Integer("With permission", readonly=True, help="How many of those carry a permission record.")
    status = fields.Selection(
        [('ok', "Working"), ('not_configured', "Not configured"), ('skipped', "Skipped"), ('blocked', "Blocked"), ('error', "Error"),
         ('disabled', "Disabled"), ('unknown', "Not run yet")], readonly=True, default='unknown')
    detail = fields.Text(readonly=True)
    last_run = fields.Datetime(readonly=True)
    last_success = fields.Datetime(readonly=True)
    last_count = fields.Integer("Items last run", readonly=True)
    failures = fields.Integer("Consecutive failures", readonly=True)

    _name_uniq = models.Constraint('unique(name)', "One health record per source.")

    @api.depends('name')
    def _compute_label(self):
        for rec in self:
            rec.label = SOURCE_LABELS.get(rec.name, rec.name)


class AiLeadgenCandidate(models.Model):
    _name = 'ai.leadgen.candidate'
    _description = "AI Lead Gen Candidate"
    _order = 'published_at desc, id desc'
    _rec_name = 'name'

    name = fields.Char("Title", readonly=True)
    url = fields.Char("Source URL", required=True, readonly=True, index=True)
    source = fields.Char(readonly=True, index=True)
    source_name = fields.Char("Community / feed", readonly=True)
    state = fields.Selection(CANDIDATE_STATES, string="Status", readonly=True, index=True)
    reason = fields.Char(readonly=True)
    intent_category = fields.Selection(INTENT_CATEGORIES, string="Intent category", readonly=True)
    intent_level = fields.Selection(INTENT_LEVELS, readonly=True)
    buyer_intent = fields.Boolean("Buyer (AI verdict)", readonly=True)
    score = fields.Integer("AI score", readonly=True)
    country = fields.Char(readonly=True, index=True)
    region = fields.Selection([('arab', "Arab country"), ('non_arab', "Outside the Arab world"), ('unknown', "Country unknown")], readonly=True, index=True)
    language = fields.Char(readonly=True)
    published_at = fields.Datetime(readonly=True)
    discovered_at = fields.Datetime(readonly=True)
    freshness = fields.Selection([('24h', "Last 24 hours"), ('7d', "Last 7 days"), ('older', "Older"), ('unknown', "Unknown")], readonly=True)
    phones_found = fields.Integer(readonly=True)
    emails_found = fields.Integer(readonly=True)
    phones_verified = fields.Integer(readonly=True)
    emails_verified = fields.Integer(readonly=True)
    attempts = fields.Integer(readonly=True)
    next_retry = fields.Char(readonly=True)
    permission = fields.Char(readonly=True)
    excerpt = fields.Text("Original request (excerpt)", readonly=True)
    lead_id = fields.Many2one('crm.lead', string="CRM lead", readonly=True, ondelete='set null')

    _url_uniq = models.Constraint('unique(url)', "A candidate exists once per source URL.")

    # -------------------------------------------------------------------------
    # SYNC from the engine's SQLite state
    # -------------------------------------------------------------------------

    @api.model
    def _sync_from_engine(self):
        """Read `main.py ops-export` and mirror it. Never raises into the cron: the error is kept for the dashboard."""
        params = self.env['ir.config_parameter'].sudo()
        if not self.env['ai.leadgen.run']._engine_available():
            params.set_param('ai_leadgen.ops_last_error', "No lead-generation engine is installed on this server.")
            return False
        try:
            code, stdout, stderr = self.env['leadgen.source']._run_engine(['ops-export'], timeout=120)
            if code:
                raise UserError(stderr.strip()[-300:] or f"exit code {code}")
            data = json.loads(stdout)
        except (UserError, OSError, ValueError, subprocess.SubprocessError) as exc:
            params.set_param('ai_leadgen.ops_last_error', str(exc)[:300])
            _logger.warning("Cannot read the engine's operations snapshot: %s", exc)
            return False
        self.sudo()._apply_snapshot(data)
        params.set_param('ai_leadgen.ops_last_sync', fields.Datetime.to_string(fields.Datetime.now()))
        params.set_param('ai_leadgen.ops_last_error', '')
        return True

    @api.model
    def _apply_snapshot(self, data):
        Health = self.env['ai.leadgen.source.health'].sudo()
        known = {h.name: h for h in Health.search([])}
        for src in data.get('sources', []):
            vals = {'enabled': src['enabled'], 'configured': src['configured'], 'permitted': src['permitted'], 'status': src['status'],
                    'detail': src['detail'], 'last_run': _dt(src['last_run_at']), 'last_success': _dt(src['last_success_at']),
                    'last_count': src['last_count'], 'failures': src['failures']}
            if src['name'] in known:
                known[src['name']].write(vals)
            else:
                Health.create({'name': src['name'], **vals})
        existing = {c.url: c for c in self.search([])}
        now = fields.Datetime.now()
        to_create = []
        for row in data.get('candidates', []):
            published = _dt(row['published_at'])
            age = (now - published).total_seconds() / 3600 if published else None
            vals = {
                'name': row['title'] or row['url'], 'source': row['source'], 'source_name': row['source_name'], 'state': row['status'], 'reason': row['reason'],
                'intent_category': row['ai_category'] or False, 'intent_level': row['intent_level'] or False, 'buyer_intent': row['buyer_intent'] == 1,
                'score': row['score'] or 0, 'country': row['country'], 'language': row['language'], 'published_at': published,
                'region': 'unknown' if row.get('arab') is None else 'arab' if row['arab'] else 'non_arab',
                'discovered_at': _dt(row['discovered_at']), 'phones_found': row['phones_found'], 'emails_found': row['emails_found'],
                'phones_verified': row['phones_verified'], 'emails_verified': row['emails_verified'], 'attempts': row['attempts'],
                'next_retry': row['next_retry'] or False, 'permission': row['permission'], 'excerpt': row['excerpt'],
                'freshness': 'unknown' if age is None else '24h' if age <= 24 else '7d' if age <= 168 else 'older',
            }
            rec = existing.get(row['url'])
            if rec:
                rec.write({k: v for k, v in vals.items() if rec[k] != v and not (k in ('intent_category', 'intent_level', 'next_retry', 'published_at', 'discovered_at') and not v and not rec[k])})
            else:
                to_create.append({'url': row['url'], 'lead_id': False, **vals, '_odoo_id': row['odoo_id']})
        leads = {lead.id for lead in self.env['crm.lead'].sudo().browse([c['_odoo_id'] for c in to_create if c['_odoo_id']]).exists()}
        for vals in to_create:
            odoo_id = vals.pop('_odoo_id')
            vals['lead_id'] = odoo_id if odoo_id in leads else False
        self.create(to_create)
        gone = self.search([('url', 'not in', [r['url'] for r in data.get('candidates', [])])])
        gone.unlink()  # the engine deleted these (for example a removed source): the mirror follows
        Health.search([('name', 'not in', [s['name'] for s in data.get('sources', [])])]).unlink()
        for row in data.get('candidates', []):  # link leads created since the last sync
            rec = existing.get(row['url'])
            if rec and row['odoo_id'] and not rec.lead_id and self.env['crm.lead'].sudo().browse(row['odoo_id']).exists():
                rec.lead_id = row['odoo_id']

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_retry(self):
        """Restart failed or waiting candidates. The engine only re-queues them: the duplicate checks run again, so no second lead can appear."""
        retryable = self.filtered(lambda c: c.state in ('error', 'deferred'))
        if not retryable:
            raise UserError(self.env._("Only failed or waiting candidates can be restarted."))
        done = 0
        for cand in retryable[:100]:
            code, stdout, _stderr = self.env['leadgen.source']._run_engine(['retry-candidate', '--url', cand.url], timeout=60)
            done += code == 0 and 'queued' in stdout
        self.env['ai.leadgen.candidate'].sudo()._sync_from_engine()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._("Restart queued"), 'message': self.env._("%s candidate(s) will be processed in the next run.", done),
                           'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}


SCOPE_DOMAIN = ['|', ('region', '=', 'arab'), '&', ('region', '=', 'unknown'), ('language', '=', 'ar')]  # the Arab world (+ Arabic text with no country)


class AiLeadgenDashboard(models.Model):
    _name = 'ai.leadgen.dashboard'
    _description = "AI Lead Gen Dashboard"

    name = fields.Char(default="Lead generation operations", readonly=True)
    sources_total = fields.Integer("Enabled sources", compute='_compute_stats')
    sources_connected = fields.Integer("Sources working", compute='_compute_stats')
    sources_unconfigured = fields.Integer("Sources needing configuration", compute='_compute_stats')
    discovered = fields.Integer("Discovered (Arab world)", compute='_compute_stats')
    outside_region = fields.Integer("Rejected: outside the Arab world", compute='_compute_stats')
    unknown_region = fields.Integer("Rejected: country unknown and not Arabic", compute='_compute_stats')
    fresh_24h = fields.Integer("Published in the last 24 hours", compute='_compute_stats')
    fresh_7d = fields.Integer("Published in the last 7 days", compute='_compute_stats')
    pending = fields.Integer("Pending candidates", compute='_compute_stats')
    processed = fields.Integer("Processed candidates", compute='_compute_stats')
    failed = fields.Integer("Failed jobs", compute='_compute_stats')
    qualified = fields.Integer("Qualified buyers (AI)", compute='_compute_stats')
    odoo_buyers = fields.Integer("Odoo-specific buyers", compute='_compute_stats')
    erp_buyers = fields.Integer("ERP buyers", compute='_compute_stats')
    software_buyers = fields.Integer("Software development buyers", compute='_compute_stats')
    ai_buyers = fields.Integer("AI automation buyers", compute='_compute_stats')
    maintenance_buyers = fields.Integer("Maintenance / customization buyers", compute='_compute_stats')
    phones_verified = fields.Integer("Verified phone numbers", compute='_compute_stats')
    emails_verified = fields.Integer("Verified emails (syntax + domain)", compute='_compute_stats')
    leads_created = fields.Integer("CRM leads created", compute='_compute_stats')
    rejected = fields.Integer("Rejected candidates", compute='_compute_stats')
    duplicates = fields.Integer(compute='_compute_stats')
    rejection_reasons = fields.Text(compute='_compute_stats')
    countries = fields.Text("Candidates by Arab country", compute='_compute_stats')
    last_sync = fields.Char("Last synchronization", compute='_compute_ops')
    last_error = fields.Char("Last sync error", compute='_compute_ops')
    next_runs = fields.Text("Scheduled runs", compute='_compute_ops')

    @api.model
    def _the_dashboard(self):
        return self.search([], limit=1) or self.create({})

    @api.model
    def action_open(self):
        return {'type': 'ir.actions.act_window', 'name': self.env._("Operations"), 'res_model': self._name,
                'res_id': self._the_dashboard().id, 'view_mode': 'form', 'target': 'current'}

    def _compute_stats(self):
        Cand, Health = self.env['ai.leadgen.candidate'].sudo(), self.env['ai.leadgen.source.health'].sudo()
        count = lambda dom: Cand.search_count(SCOPE_DOMAIN + dom)  # noqa: E731  - every figure below is about the Arab world only
        enabled = Health.search([('enabled', '=', True)])
        sums = Cand._read_group(SCOPE_DOMAIN, aggregates=['phones_verified:sum', 'emails_verified:sum'])[0]
        reasons = Cand._read_group(SCOPE_DOMAIN + [('state', 'in', ('rejected', 'error'))], ['reason'], ['__count'], order='__count desc', limit=12)
        countries = Cand._read_group(SCOPE_DOMAIN, ['country'], ['__count'], order='__count desc', limit=12)
        outside = Cand.search_count([('region', '=', 'non_arab')])
        unknown = Cand.search_count([('region', '=', 'unknown'), ('language', '!=', 'ar')])
        for rec in self:
            buyers = [('buyer_intent', '=', True)]
            rec.sources_total = len(enabled)
            rec.sources_connected = len(enabled.filtered(lambda h: h.status == 'ok'))
            rec.sources_unconfigured = len(enabled.filtered(lambda h: h.status == 'not_configured'))
            rec.discovered = count([])
            rec.outside_region, rec.unknown_region = outside, unknown
            rec.fresh_24h = count([('freshness', '=', '24h')])
            rec.fresh_7d = count([('freshness', 'in', ('24h', '7d'))])
            rec.pending = count([('state', 'in', ('new', 'deferred'))])
            rec.processed = count([('state', 'in', ('pushed', 'rejected', 'duplicate'))])
            rec.failed = count([('state', '=', 'error')])
            rec.qualified = count(buyers)
            rec.odoo_buyers = count(buyers + [('intent_category', '=', 'odoo_buyer')])
            rec.erp_buyers = count(buyers + [('intent_category', '=', 'erp_buyer')])
            rec.software_buyers = count(buyers + [('intent_category', '=', 'software_dev_buyer')])
            rec.ai_buyers = count(buyers + [('intent_category', '=', 'ai_automation_buyer')])
            rec.maintenance_buyers = count(buyers + [('intent_category', '=', 'maintenance_customization_buyer')])
            rec.phones_verified, rec.emails_verified = sums[0] or 0, sums[1] or 0
            rec.leads_created = count([('state', '=', 'pushed')])
            rec.rejected = count([('state', '=', 'rejected')])
            rec.duplicates = count([('state', '=', 'duplicate')])
            rec.rejection_reasons = "\n".join(f"{r or '-'}: {n}" for r, n in reasons) or False
            rec.countries = "\n".join(f"{c or '-'}: {n}" for c, n in countries) or False

    def _compute_ops(self):
        params = self.env['ir.config_parameter'].sudo()
        crons = []
        for xmlid in ('ai_leadgen.ir_cron_scheduled_run', 'ai_leadgen.ir_cron_sync_ops'):
            cron = self.env.ref(xmlid, raise_if_not_found=False)
            if cron and cron.active:
                crons.append(f"{cron.name}: {fields.Datetime.to_string(cron.nextcall)} UTC")
        for rec in self:
            rec.last_sync = params.get_param('ai_leadgen.ops_last_sync') or self.env._("never")
            rec.last_error = params.get_param('ai_leadgen.ops_last_error') or False
            rec.next_runs = "\n".join(crons) or self.env._("No collection run is scheduled. Enable \"AI Lead Gen: scheduled run\" in Settings > Technical > Scheduled Actions.")

    def action_refresh(self):
        self.env['ai.leadgen.candidate'].sudo()._sync_from_engine()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_run_ingestion(self):
        run = self.env['ai.leadgen.run'].create({'source': 'all', 'hours': 168, 'limit': 100})
        run.action_start()
        return {'type': 'ir.actions.act_window', 'res_model': 'ai.leadgen.run', 'res_id': run.id, 'view_mode': 'form'}

    def action_retry_failed(self):
        failed = self.env['ai.leadgen.candidate'].sudo().search([('state', 'in', ('error', 'deferred'))])
        if not failed:
            raise UserError(self.env._("There are no failed or waiting candidates."))
        return failed.action_retry()
