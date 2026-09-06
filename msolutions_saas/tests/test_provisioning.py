from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestSaasProvisioning(TransactionCase):
    """Section 1: stale-provisioning watchdog + retry.

    None of these touch a real tenant database. The methods that would --
    _database_exists, _drop_database_raw, _provision_database, _provision_odoo,
    _grant_web_ownership -- each wrap raw psycopg / odoo.service.db side effects,
    and are patched here to stand in for those so the control-plane logic
    (state machine, ordering, guards) can be tested without a Postgres cluster
    full of tenants.
    """

    def setUp(self):
        super().setUp()
        self.Tenant = self.env["saas.tenant"]
        # The control-plane guard depends on odoo.conf (saas_control_db) and is
        # not what these tests exercise, so neutralise it for all of them.
        p = patch.object(type(self.Tenant), "_assert_control_plane",
                         lambda self: None)
        p.start()
        self.addCleanup(p.stop)

    def _tenant(self, **vals):
        vals.setdefault("name", "acmetest")
        return self.Tenant.create(vals)

    # -- name validation -------------------------------------------------

    def test_name_too_short_message_says_3_to_31(self):
        # NAME_RE (^[a-z][a-z0-9]{2,30}$) allows 3..31 chars; the message must
        # not claim 4 -- that was the off-by-one being fixed.
        with self.assertRaises(ValidationError) as err:
            self._tenant(name="ab")
        self.assertIn("3 to 31", str(err.exception))

    def test_three_char_name_is_accepted(self):
        # Boundary: exactly 3 characters is valid.
        self.assertEqual(self._tenant(name="abc").name, "abc")

    # -- watchdog --------------------------------------------------------

    def _run_watchdog(self):
        # Neutralise the per-tenant commit so the test transaction stays intact.
        with patch.object(self.env.cr, "commit", lambda: None):
            self.Tenant._cron_watchdog()

    def test_watchdog_moves_stuck_provisioning_to_error(self):
        t = self._tenant()
        t.write({"state": "provisioning",
                 "state_since": fields.Datetime.now() - timedelta(minutes=30)})
        self._run_watchdog()
        self.assertEqual(t.state, "error")
        self.assertIn("Timed out", t.error_message)

    def test_watchdog_moves_stuck_terminating_to_error(self):
        t = self._tenant()
        t.write({"state": "terminating",
                 "state_since": fields.Datetime.now() - timedelta(minutes=30)})
        self._run_watchdog()
        self.assertEqual(t.state, "error")

    def test_watchdog_leaves_a_fresh_run_alone(self):
        t = self._tenant()
        t.write({"state": "provisioning", "state_since": fields.Datetime.now()})
        self._run_watchdog()
        self.assertEqual(t.state, "provisioning")

    # -- retry -----------------------------------------------------------

    def test_retry_refuses_when_ever_active(self):
        # A tenant that was ever live holds customer data; retry re-provisions
        # (which drops the database), so it must refuse.
        t = self._tenant()
        t.write({"state": "error", "ever_active": True})
        with self.assertRaises(UserError):
            t.action_retry()

    def test_retry_only_valid_from_error(self):
        t = self._tenant()
        t.write({"state": "active", "ever_active": True})
        with self.assertRaises(UserError):
            t.action_retry()

    def test_retry_requeues_a_failed_provision(self):
        t = self._tenant()
        t.write({"state": "error", "ever_active": False})
        with patch.object(type(t), "_trigger_cron", lambda self: None):
            t.action_retry()
        self.assertEqual(t.state, "provisioning")
        self.assertFalse(t.error_message)

    def test_run_provision_drops_half_db_before_recreating(self):
        # The core of the retry fix: when a database from a failed attempt still
        # exists, _run_provision must DROP it BEFORE _provision_database, or
        # create fails with "database already exists".
        t = self._tenant()
        t.write({"state": "provisioning", "ever_active": False})
        order = []
        with patch.object(type(t), "_database_exists", lambda self: True), \
             patch.object(type(t), "_drop_database_raw", lambda self: order.append("drop")), \
             patch.object(type(t), "_provision_database", lambda self: order.append("create")), \
             patch.object(type(t), "_provision_odoo", lambda self: order.append("init")), \
             patch.object(type(t), "_grant_web_ownership", lambda self: order.append("grant")), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()
        self.assertEqual(order, ["drop", "create", "init", "grant"])
        self.assertEqual(t.state, "active")
        self.assertTrue(t.ever_active)

    def test_run_provision_fresh_does_not_drop(self):
        # A first-time provision (no existing database) must never call drop.
        t = self._tenant()
        t.write({"state": "provisioning", "ever_active": False})
        order = []
        with patch.object(type(t), "_database_exists", lambda self: False), \
             patch.object(type(t), "_drop_database_raw", lambda self: order.append("drop")), \
             patch.object(type(t), "_provision_database", lambda self: order.append("create")), \
             patch.object(type(t), "_provision_odoo", lambda self: None), \
             patch.object(type(t), "_grant_web_ownership", lambda self: None), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()
        self.assertNotIn("drop", order)
        self.assertEqual(t.state, "active")

    # -- template provisioning ------------------------------------------

    def _plan(self, template_db="tpl_test"):
        return self.env["saas.plan"].create(
            {"name": "T", "template_db": template_db})

    def test_run_provision_template_path_scrubs_not_initialises(self):
        # When _provision_database reports a clone, _run_provision must scrub
        # the copy (_post_copy_cleanup) and NEVER re-initialise it (base is in
        # the template). It still calls _install_extra_modules -- which installs
        # only the delta beyond the template's standard apps -- then grants.
        t = self._tenant()
        t.write({"state": "provisioning", "ever_active": False})
        order = []
        with patch.object(type(t), "_database_exists", lambda self: False), \
             patch.object(type(t), "_provision_database",
                          lambda self: order.append("clone") or True), \
             patch.object(type(t), "_post_copy_cleanup",
                          lambda self: order.append("cleanup")), \
             patch.object(type(t), "_provision_odoo",
                          lambda self: order.append("init")), \
             patch.object(type(t), "_install_extra_modules",
                          lambda self: order.append("modules")), \
             patch.object(type(t), "_grant_web_ownership",
                          lambda self: order.append("grant")), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()
        # Clone, scrub, install the delta, then hand ownership to odoo_web.
        # Crucially: NO re-initialisation.
        self.assertEqual(order, ["clone", "cleanup", "modules", "grant"])
        self.assertNotIn("init", order)
        self.assertEqual(t.state, "active")
        self.assertTrue(t.ever_active)

    def test_install_extra_modules_skips_apps_already_in_clone(self):
        # The delta logic: an app already installed in the (cloned) database is
        # not re-installed. With every requested app already present, no
        # subprocess runs at all.
        mod = self.env["ir.module.module"].search([], limit=1)
        t = self._tenant()
        t.module_ids = mod
        ran = []
        # Pretend the clone already has the requested module installed. closing()
        # calls .close() on the cursor, so the fake must provide it.
        class _Cur:
            def execute(self, *a): pass
            def fetchall(self): return [(mod.name,)]
            def close(self): pass
        with patch("odoo.sql_db.db_connect",
                   lambda name: type("C", (), {"cursor": lambda self: _Cur()})()), \
             patch("subprocess.run", lambda *a, **k: ran.append(a)):
            t._install_extra_modules()
        self.assertEqual(ran, [])  # nothing to install -> no subprocess

    def test_run_provision_scratch_path_installs_modules(self):
        # When _provision_database reports no clone, the from-scratch path runs
        # (_provision_odoo + _install_extra_modules) and cleanup does NOT.
        t = self._tenant()
        t.write({"state": "provisioning", "ever_active": False})
        order = []
        with patch.object(type(t), "_database_exists", lambda self: False), \
             patch.object(type(t), "_provision_database",
                          lambda self: order.append("create") or False), \
             patch.object(type(t), "_post_copy_cleanup",
                          lambda self: order.append("cleanup")), \
             patch.object(type(t), "_provision_odoo",
                          lambda self: order.append("init")), \
             patch.object(type(t), "_install_extra_modules",
                          lambda self: order.append("modules")), \
             patch.object(type(t), "_grant_web_ownership",
                          lambda self: order.append("grant")), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()
        self.assertEqual(order, ["create", "init", "modules", "grant"])
        self.assertNotIn("cleanup", order)
        self.assertEqual(t.state, "active")

    def test_template_db_none_without_plan(self):
        # No plan -> build from scratch.
        self.assertIsNone(self._tenant()._template_db())

    def test_template_db_falls_back_when_template_missing(self):
        # A missing template must never break provisioning: fall back to scratch.
        t = self._tenant()
        t.plan_id = self._plan("tpl_gone")
        with patch.object(type(t), "_database_exists_named",
                          lambda self, name: False):
            self.assertIsNone(t._template_db())

    def test_template_db_returns_name_when_present(self):
        t = self._tenant()
        t.plan_id = self._plan("tpl_here")
        with patch.object(type(t), "_database_exists_named",
                          lambda self, name: True):
            self.assertEqual(t._template_db(), "tpl_here")

    def test_provision_database_true_on_clone_false_on_scratch(self):
        t = self._tenant()
        t.plan_id = self._plan("tpl_present")
        with patch.object(type(t), "_provision_from_template",
                          lambda self, tpl: None), \
             patch.object(type(t), "_database_exists_named",
                          lambda self, name: True):
            self.assertTrue(t._provision_database())
        t2 = self._tenant(name="acmetwo")
        with patch.object(type(t2), "_create_empty_database", lambda self: None):
            self.assertFalse(t2._provision_database())

    # -- discovery & complete deletion ----------------------------------

    def test_discover_imports_untracked_databases_only(self):
        # Server reports: a tracked db, an untracked tenant, a template, a
        # reserved name. Only the untracked tenant-shaped name is imported.
        self._tenant(name="known1")
        rows = [("known1",), ("newtenant",), ("tpl_basic",), ("admin",)]

        class _Cur:
            def execute(self, *a): pass
            def fetchall(self): return rows
            def close(self): pass
        with patch("odoo.sql_db.db_connect",
                   lambda name: type("C", (), {"cursor": lambda self: _Cur()})()):
            n = self.Tenant._discover_tenants()
        self.assertEqual(n, 1)
        new = self.Tenant.search([("name", "=", "newtenant")])
        self.assertTrue(new)
        self.assertEqual(new.state, "active")
        self.assertTrue(new.ever_active)
        self.assertFalse(self.Tenant.search([("name", "=", "tpl_basic")]))
        self.assertFalse(self.Tenant.search([("name", "=", "admin")]))

    def test_run_drop_removes_record_leaving_no_trace(self):
        # A successful drop must delete the record itself, not park it in
        # 'terminated' -- no lingering pointer to the tenant.
        t = self._tenant()
        t.write({"state": "terminating"})
        with patch.object(type(t), "_drop_database", lambda self: None), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_drop()
        self.assertFalse(t.exists())

    def test_new_tenant_defaults_to_configured_plan(self):
        # The fix for "create from the view takes a minute": a new tenant picks
        # up the configured Default Plan, so it clones from a template instead
        # of building from scratch.
        plan = self._plan("tpl_default")
        self.env["saas.config"]._get().default_plan_id = plan
        self.assertEqual(self.Tenant.create({"name": "acmedef"}).plan_id, plan)

    def test_run_drop_keeps_record_on_failure(self):
        # If the drop fails, the record must survive (in error) so the operator
        # can see it and retry -- the opposite of the success path.
        t = self._tenant()
        t.write({"state": "terminating"})

        def _boom(self):
            raise RuntimeError("drop failed")
        with patch.object(type(t), "_drop_database", _boom), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_drop()
        self.assertTrue(t.exists())
        self.assertEqual(t.state, "error")
