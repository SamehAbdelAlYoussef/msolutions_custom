import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import psutil

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import new_test_user

from odoo.addons.base.tests.common import BaseCommon

ENGINE = 'odoo.addons.ai_leadgen.models.leadgen_source.LeadgenSource._run_engine'


class FakeEngine:
    """Stands in for `main.py`: records what Odoo sent and answers with a queue."""

    def __init__(self, queue=None, resolve=None, code=0):
        self.calls, self.queue, self.resolve, self.code = [], queue or [], resolve, code

    def __call__(self, args, payload=None, timeout=60, uncapped=False):
        self.calls.append((list(args), payload, uncapped))
        if args[0] == 'resolve-group':
            return self.code, json.dumps(self.resolve or {}), ''
        return self.code, json.dumps(self.queue), ''

    @property
    def last_sync(self):
        return [c for c in self.calls if c[0][0] == 'sync-groups'][-1][1]


def match(name, url, members=''):
    return {'name': name, 'url': url, 'members': members}


class EnginePresent:
    """These tests replace the engine call itself, so they pretend an engine is installed (it is not, on a SaaS server that only ships the module)."""

    def setUp(self):
        super().setUp()
        patcher = patch('odoo.addons.ai_leadgen.models.ai_leadgen_run.AiLeadgenRun._engine_available', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestLeadgenSource(EnginePresent, BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        manager = new_test_user(cls.env, login='ai_leadgen_source_mgr', groups='sales_team.group_sale_manager')
        cls.env = cls.env(user=manager)
        cls.Source = cls.env['leadgen.source']

    def sync(self, **kw):
        engine = FakeEngine(**kw)
        patcher = patch(ENGINE, side_effect=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return engine

    # ---- seeded list and the drag-and-drop handle
    def test_the_module_ships_no_tenant_data_and_the_groups_are_an_explicit_idempotent_setup_step(self):
        from odoo.addons.ai_leadgen.models.leadgen_source import DEFAULT_GROUPS
        urls = [f'https://www.facebook.com/groups/{gid}' for _name, gid in DEFAULT_GROUPS]
        self.Source.sudo().with_context(active_test=False).search([('url', 'in', urls)]).unlink()      # a fresh tenant has none of them
        self.assertEqual(self.Source.sudo().search_count([('url', 'in', urls)]), 0)
        self.assertEqual(self.Source._seed_default_groups(), 10)
        groups = self.Source.sudo().search([('url', 'in', urls)])
        self.assertEqual(groups.mapped('sequence'), sorted(groups.mapped('sequence')))
        self.assertTrue(all(g.verified and g.enabled for g in groups))
        self.assertEqual(self.Source._seed_default_groups(), 0)                                           # running it again changes nothing
        self.assertNotIn('data/leadgen_source_data.xml', __import__('odoo.modules', fromlist=['Manifest']).Manifest.for_addon('ai_leadgen')['data'])

    def test_list_view_has_the_handle_as_first_column_and_the_resolve_button(self):
        arch = self.Source.get_view(self.env.ref('ai_leadgen.leadgen_source_view_list').id)['arch']
        self.assertLess(arch.index('widget="handle"'), arch.index('name="name"'))
        self.assertIn('widget="handle"', arch.split('<field')[1])           # the very first field
        for needle in ('name="url"', 'widget="boolean_toggle"', 'action_search_facebook_group'):
            self.assertIn(needle, arch)
        self.assertEqual(self.Source._fields['sequence'].default(self.Source), 10)

    # ---- URL entry
    def test_pasting_a_group_link_normalises_and_verifies_it_and_reaches_the_engine(self):
        engine = self.sync()
        group = self.Source.create({'name': 'Odoo Test', 'url': ' https://m.facebook.com/groups/odootest/?ref=x '})
        self.assertEqual((group.url, group.verified, group.status, group.sequence), ('https://www.facebook.com/groups/odootest', True, 'polled', 10))
        sent = [p for p in engine.last_sync if p['url'] == group.url][0]
        self.assertEqual((sent['enabled'], sent['verified'], sent['sequence']), (True, True, 10))

    def test_a_link_that_is_not_a_facebook_group_is_refused(self):
        self.sync()
        for bad in ('https://example.com/groups/1', 'https://www.facebook.com/groups/joins/', 'odoo egypt'):
            with self.assertRaises(ValidationError):
                self.Source.create({'name': 'x', 'url': bad})

    def test_the_same_group_cannot_be_listed_twice(self):
        self.sync()
        self.Source.create({'name': 'A', 'url': 'https://www.facebook.com/groups/dup'})
        with self.assertRaises(Exception), self.cr.savepoint():
            self.Source.create({'name': 'B', 'url': 'https://www.facebook.com/groups/dup/'})

    def test_a_name_without_url_waits_and_is_never_polled(self):
        engine = self.sync()
        group = self.Source.create({'name': 'Odoo Gulf'})
        self.assertEqual((group.status, group.verified), ('needs_url', False))
        sent = [p for p in engine.last_sync if p['name'] == 'Odoo Gulf'][0]
        self.assertFalse(sent['url'])

    def test_switching_a_group_off_reaches_the_engine(self):
        engine = self.sync()
        group = self.Source.create({'name': 'A', 'url': 'https://www.facebook.com/groups/aaa'})
        group.enabled = False
        self.assertEqual(group.status, 'off')
        self.assertFalse([p for p in engine.last_sync if p['url'] == group.url][0]['enabled'])

    # ---- priority
    def test_dragging_a_group_to_the_top_changes_the_priority_the_engine_receives(self):
        engine = self.sync()
        groups = self.Source.create([{'name': f'G{i}', 'url': f'https://www.facebook.com/groups/g{i}', 'sequence': 1000 + i} for i in range(4)])
        groups[3].sequence = 999                                           # what the handle writes after a drag
        order = [p['name'] for p in sorted((p for p in engine.last_sync if p['name'].startswith('G')), key=lambda p: p['sequence'])]
        self.assertEqual(order, ['G3', 'G0', 'G1', 'G2'])
        self.assertEqual(self.Source.search([('name', 'in', ['G0', 'G1', 'G2', 'G3'])]).mapped('name'), ['G3', 'G0', 'G1', 'G2'])

    def test_engine_visit_times_and_weights_come_back(self):
        group_url = 'https://www.facebook.com/groups/back'
        self.sync(queue=[{'url': group_url, 'name': 'Back', 'relevant': True, 'last_visited_at': '2026-11-14T20:13:55+00:00', 'weight': 3}])
        group = self.Source.create({'name': 'Back', 'url': group_url})
        self.assertEqual((group.weight, str(group.last_visited), group.engine_synced), (3, '2026-11-14 20:13:55', True))

    # ---- Search / Resolve URL
    def test_search_by_name_fills_exact_name_url_and_members_and_verifies(self):
        engine = self.sync(resolve={'query': 'odoo mates', 'matches': [match('Odoo Mates', 'https://www.facebook.com/groups/odoomates', '12K members'),
                                                                        match('Odoo Mates 2', 'https://www.facebook.com/groups/other', '9 members')]})
        group = self.Source.create({'name': 'odoo mates'})
        result = group.action_search_facebook_group()
        self.assertEqual((group.name, group.url, group.member_count, group.verified), ('Odoo Mates', 'https://www.facebook.com/groups/odoomates', '12K members', True))
        self.assertIn('Odoo Mates 2', group.resolve_note)
        self.assertEqual(result['params']['type'], 'success')
        call = [c for c in engine.calls if c[0][0] == 'resolve-group'][0]
        self.assertEqual(call[0], ['resolve-group', '--name', 'odoo mates'])
        self.assertTrue(call[2])                                           # Chromium gets the address space Odoo's cap would take away

    def test_the_exact_group_is_chosen_even_when_facebook_ranks_it_lower(self):
        self.sync(resolve={'matches': [match('Odoo ERP Solutions', 'https://www.facebook.com/groups/1', '1.7K members'),
                                       match('Odoo Mates', 'https://www.facebook.com/groups/odoomatesofficial', '302 members')]})
        group = self.Source.create({'name': 'Odoo Mates'})
        group.action_search_facebook_group()
        self.assertEqual((group.url, group.member_count, group.verified), ('https://www.facebook.com/groups/odoomatesofficial', '302 members', True))

    def test_an_inexact_match_waits_for_the_user_to_confirm_before_polling(self):
        self.sync(resolve={'matches': [match('Odoo ERP Egypt Group', 'https://www.facebook.com/groups/777', '4,300 members')]})
        group = self.Source.create({'name': 'Odoo Egypt ERP'})
        result = group.action_search_facebook_group()
        self.assertEqual((group.name, group.url, group.verified, group.status), ('Odoo Egypt ERP', 'https://www.facebook.com/groups/777', False, 'needs_confirm'))
        self.assertEqual(result['params']['type'], 'warning')
        group.action_confirm()
        self.assertEqual((group.verified, group.status), (True, 'polled'))

    def test_search_without_results_or_with_an_engine_error_changes_nothing(self):
        self.sync(resolve={'matches': []})
        group = self.Source.create({'name': 'Nope'})
        group.action_search_facebook_group()
        self.assertFalse(group.url)
        self.assertIn('no group', group.resolve_note)
        self.sync(resolve={'error': 'quiet hours (01:00-07:00 Africa/Cairo)'}, code=3)
        with self.assertRaisesRegex(UserError, 'quiet hours'):
            group.action_search_facebook_group()
        self.assertFalse(group.url)

    def test_confirm_needs_a_url(self):
        self.sync()
        with self.assertRaises(UserError):
            self.Source.create({'name': 'No URL'}).action_confirm()

    # ---- failure handling and the other direction
    def test_an_unreachable_engine_never_blocks_the_edit(self):
        with patch(ENGINE, side_effect=OSError('no engine')):
            group = self.Source.create({'name': 'Offline', 'url': 'https://www.facebook.com/groups/offline'})
        self.assertFalse(group.engine_synced)
        self.assertEqual(group.status, 'polled')                           # saved in Odoo; the cron pushes it later

    def test_deleting_a_group_stops_it_being_polled(self):
        engine = self.sync()
        group = self.Source.create({'name': 'Bye', 'url': 'https://www.facebook.com/groups/bye'})
        group.unlink()
        sent = [p for p in engine.last_sync if p['url'] == 'https://www.facebook.com/groups/bye']
        self.assertEqual([p['enabled'] for p in sent], [False])

    def test_groups_the_engine_found_are_listed_at_the_end(self):
        known = self.Source.search([], order='sequence desc', limit=1)
        self.sync(queue=[{'url': 'https://www.facebook.com/groups/found', 'name': 'Found System Group', 'relevant': True, 'last_visited_at': None, 'weight': 1},
                         {'url': 'https://www.facebook.com/groups/ignored', 'name': 'Cars', 'relevant': False, 'last_visited_at': None, 'weight': 1}])
        action = self.Source.action_import_from_engine()
        new = self.Source.search([('url', '=', 'https://www.facebook.com/groups/found')])
        self.assertEqual((new.name, new.source, new.verified, new.sequence > known.sequence), ('Found System Group', 'engine', True, True))
        self.assertFalse(self.Source.search([('name', '=', 'Cars')]))
        self.assertIn('1 group', action['params']['message'])

    # ---- strict browser cleanup
    def test_a_stuck_engine_is_killed_with_everything_it_started(self):
        directory = Path(tempfile.mkdtemp(prefix='ai_leadgen_stuck_'))
        pidfile = directory / 'child.pid'
        (directory / 'main.py').write_text(
            'import subprocess, sys, time, pathlib\n'
            f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
            'time.sleep(600)\n')
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ai_leadgen.engine_path', str(directory))
        params.set_param('ai_leadgen.python_path', sys.executable)
        with self.assertRaisesRegex(UserError, 'stopped'):
            self.Source._run_engine(['resolve-group', '--name', 'x'], timeout=3)
        child = int(pidfile.read_text())
        deadline = time.time() + 5
        while psutil.pid_exists(child) and time.time() < deadline:
            try:
                if psutil.Process(child).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.2)
        alive = psutil.pid_exists(child) and psutil.Process(child).status() != psutil.STATUS_ZOMBIE
        self.assertFalse(alive, 'the child process survived the timeout')


class TestIngestionFields(BaseCommon):
    """Fields and run options added for the buyer-request ingestion sources."""

    def test_lead_keeps_intent_category_verification_status_and_publication_time(self):
        lead = self.env['crm.lead'].create({
            'name': 'Buyer request', 'ai_intent_category': 'odoo_buyer', 'ai_source_published': '2026-11-14 10:00:00',
            'ai_contact_status': 'email verified'})
        self.assertEqual((lead.ai_intent_category, str(lead.ai_source_published)), ('odoo_buyer', '2026-11-14 10:00:00'))
        categories = dict(self.env['crm.lead']._fields['ai_intent_category'].selection)
        self.assertEqual(set(categories), {'odoo_buyer', 'erp_buyer', 'software_dev_buyer', 'ai_automation_buyer',
                                           'maintenance_customization_buyer', 'unqualified'})

    def test_run_options_are_only_the_real_sources_and_facebook_is_still_there(self):
        options = dict(self.env['ai.leadgen.run']._fields['source'].selection)
        self.assertEqual(set(options), {'all', 'community', 'telegram', 'maps'})
        self.assertNotIn('ingest', options)



class TestScheduledSyncNeverAddsGroups(EnginePresent, BaseCommon):

    def test_the_scheduled_sync_does_not_import_groups_the_engine_marks_relevant(self):
        before = self.env['leadgen.source'].search_count([])
        queue = [{'url': 'https://www.facebook.com/groups/not-listed-in-odoo', 'name': 'Not listed', 'relevant': True, 'last_visited_at': None, 'weight': 1}]
        with patch(ENGINE, return_value=(0, json.dumps(queue), '')):
            self.env['leadgen.source']._cron_sync_engine()
        self.assertEqual(self.env['leadgen.source'].search_count([]), before)
        # the explicit "Import groups from the engine" action still exists for a person who wants it
        with patch(ENGINE, return_value=(0, json.dumps(queue), '')):
            self.env['leadgen.source'].action_import_from_engine()
        self.assertEqual(self.env['leadgen.source'].search_count([]), before + 1)


class TestNoEngineHere(BaseCommon):
    """A SaaS server ships the module but not the engine: every engine-facing job must stay silent."""

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('ai_leadgen.engine_path', '/nonexistent/engine')

    def test_engine_jobs_do_nothing_and_log_nothing_when_there_is_no_engine(self):
        self.assertFalse(self.env['ai.leadgen.run']._engine_available())
        with self.assertNoLogs('odoo.addons.ai_leadgen', level='WARNING'):
            self.assertFalse(self.env['ai.leadgen.candidate']._sync_from_engine())
            self.env['leadgen.source']._cron_sync_engine()
            self.assertEqual(self.env['ai.leadgen.session']._read_engine_status(), {})
            group = self.env['leadgen.source'].create({'name': 'A group', 'url': 'https://www.facebook.com/groups/noengine'})     # editing the list needs no engine
        self.assertEqual(group.status, 'polled')
        self.assertIn('No lead-generation engine', self.env['ir.config_parameter'].sudo().get_param('ai_leadgen.ops_last_error'))
