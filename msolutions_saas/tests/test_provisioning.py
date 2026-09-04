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
