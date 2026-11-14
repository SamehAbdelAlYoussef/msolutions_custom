from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestSaasTrial(TransactionCase):
    """Trial / Active customer split: 15-day trial, promote, expire."""

    def setUp(self):
        super().setUp()
        self.Tenant = self.env["saas.tenant"]
        p = patch.object(type(self.Tenant), "_assert_control_plane",
                         lambda self: None)
        p.start()
        self.addCleanup(p.stop)

    def _trial(self, name="trialco", days=15, **vals):
        return self.Tenant.create(dict(
            name=name, state="active", is_active_customer=False,
            trial_end=fields.Datetime.now() + timedelta(days=days), **vals))

    def test_staff_created_tenant_is_active_by_default(self):
        t = self.Tenant.create({"name": "staffco"})
        self.assertTrue(t.is_active_customer)
        self.assertEqual(t.trial_days_left, 0)

    def test_trial_deadline_is_15_days(self):
        delta = self.Tenant._trial_deadline() - fields.Datetime.now()
        self.assertIn(delta.days, (14, 15))  # 15-day window (sub-second skew)
        self.assertEqual(self.Tenant.TRIAL_DAYS, 15)

    def test_days_left_counts_down_and_rounds_up(self):
        t = self._trial(days=15)
        self.assertEqual(t.trial_days_left, 15)
        t.trial_end = fields.Datetime.now() + timedelta(hours=5)
        self.assertEqual(t.trial_days_left, 1)   # part of a day still counts

    def test_expire_cron_marks_only_finished_trials(self):
        live = self._trial("livetrial", days=3)
        done = self._trial("donetrial", days=-1)
        active = self.Tenant.create({"name": "paidco", "state": "active"})
        with patch.object(self.env.cr, "commit", lambda: None):
            self.Tenant._cron_expire_trials()
        self.assertTrue(done.trial_expired)
        self.assertFalse(live.trial_expired)
        self.assertFalse(active.trial_expired)
        self.assertEqual(done.trial_days_left, 0)

    def test_promoting_to_active_lifts_expiry(self):
        t = self._trial(days=-1)
        t.trial_expired = True
        t.is_active_customer = True
        self.assertFalse(t.trial_expired)
        self.assertTrue(t.is_active_customer)

    def test_moving_back_to_trial_starts_a_fresh_window(self):
        t = self.Tenant.create({"name": "backco", "state": "active"})
        t.is_active_customer = False
        self.assertFalse(t.trial_expired)
        self.assertEqual(t.trial_days_left, 15)

    def test_tier_changes_are_audited(self):
        t = self.Tenant.create({"name": "auditco", "state": "active"})
        t.is_active_customer = False
        t.is_active_customer = True
        actions = self.env["saas.audit.log"].search(
            [("tenant_name", "=", "auditco")]).mapped("action")
        self.assertIn("moved_to_trial", actions)
        self.assertIn("promoted_active", actions)

    def test_dashboard_counters_and_lists(self):
        self._trial("trialone", days=10)
        self._trial("trialtwo", days=2)             # ending soon
        done = self._trial("trialthree", days=-1)
        done.trial_expired = True
        self.Tenant.create({"name": "activeone", "state": "active"})
        group = self.env.ref("msolutions_saas.group_saas_developer")
        user = self.env["res.users"].create({"name": "Dev", "login": "dev_trial@test.com"})
        user.write({"group_ids": [(4, group.id)]})
        d = self.Tenant.with_user(user).simple_dashboard_data()
        self.assertGreaterEqual(d["demo"], 3)
        self.assertGreaterEqual(d["ending"], 1)
        self.assertGreaterEqual(d["expired"], 1)
        self.assertGreaterEqual(d["active"], 1)
        names = [x["name"] for x in d["demos"]]
        # expired first, then fewest days left
        self.assertLess(names.index("trialthree"), names.index("trialtwo"))
        self.assertLess(names.index("trialtwo"), names.index("trialone"))
        self.assertTrue(d["recent"])

    def test_demo_customer_cannot_be_backed_up(self):
        t = self._trial("nobackup", days=5)
        group = self.env.ref("msolutions_saas.group_saas_developer")
        user = self.env["res.users"].create({"name": "Dev2", "login": "dev_bk@test.com"})
        user.write({"group_ids": [(4, group.id)]})
        with self.assertRaises(UserError) as err:
            t.with_user(user).action_backup_now()
        self.assertIn("demo", str(err.exception).lower())

    # -- 30-day purge ------------------------------------------------------

    def _purge(self):
        with patch.object(self.env.cr, "commit", lambda: None), \
             patch.object(type(self.Tenant), "_trigger_cron", lambda self: None):
            self.Tenant._cron_purge_demos()

    def test_purge_queues_only_demos_past_30_days(self):
        # demo started 31 days ago: trial_end = start + 15d = 16 days ago
        old = self._trial("olddemo", days=-16)
        young = self._trial("younger", days=-10)        # started 25 days ago
        fresh = self._trial("freshone", days=10)
        self._purge()
        self.assertEqual(old.state, "terminating")
        self.assertEqual(young.state, "active")
        self.assertEqual(fresh.state, "active")

    def test_purge_never_touches_active_customers(self):
        paid = self.Tenant.create({"name": "paidold", "state": "active",
                                   "trial_end": fields.Datetime.now() - timedelta(days=400)})
        self.assertTrue(paid.is_active_customer)
        self._purge()
        self.assertEqual(paid.state, "active")

    def test_promoted_demo_is_not_purged(self):
        t = self._trial("savedone", days=-20)
        t.is_active_customer = True
        self._purge()
        self.assertEqual(t.state, "active")

    def test_demo_without_end_date_is_not_purged(self):
        t = self.Tenant.create({"name": "noenddate", "state": "active",
                                "is_active_customer": False})
        t.trial_end = False
        self._purge()
        self.assertEqual(t.state, "active")

    def test_purge_is_logged(self):
        self._trial("loggedone", days=-16)
        self._purge()
        log = self.env["saas.audit.log"].search(
            [("tenant_name", "=", "loggedone"), ("action", "=", "drop_queued")])
        self.assertTrue(log)


class TestSaasTenantSettings(TransactionCase):
    """Hide Apps + user count: the tenant database is faked, never touched."""

    class _Cur:
        def __init__(self, log, count):
            self.log, self.count = log, count
            self._cnx = type("C", (), {"commit": lambda s: log.append(("commit",))})()
        def execute(self, sql, params=None):
            self.log.append((" ".join(str(sql).split()), params))
        def fetchone(self):
            return (self.count,)
        def close(self):
            pass

    class _Conn:
        def __init__(self, cur):
            self.cur = cur
        def cursor(self):
            return self.cur

    def setUp(self):
        super().setUp()
        self.Tenant = self.env["saas.tenant"]
        self.log = []
        cur = self._Cur(self.log, 7)
        p = patch("odoo.sql_db.db_connect", lambda name: self._Conn(cur))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(type(self.Tenant), "_assert_control_plane", lambda self: None)
        p2.start()
        self.addCleanup(p2.stop)

    def _dev(self):
        group = self.env.ref("msolutions_saas.group_saas_developer")
        u = self.env["res.users"].create({"name": "Dev3", "login": "dev_set@test.com"})
        u.write({"group_ids": [(4, group.id)]})
        return u

    def test_apps_are_hidden_by_default(self):
        self.assertTrue(self.Tenant.create({"name": "newcust"}).hide_apps)

    def test_toggle_updates_the_tenant_database_and_the_cache(self):
        t = self.Tenant.create({"name": "togglec", "state": "active"})
        t.hide_apps = False
        sqls = [x[0] for x in self.log]
        self.assertTrue(any("UPDATE ir_ui_menu" in s for s in sqls))
        self.assertTrue(any("orm_signaling_default_id_seq" in s for s in sqls))
        update = [x for x in self.log if x[0].startswith("WITH RECURSIVE")][-1]
        self.assertEqual(update[1], (True,))          # shown -> active = True
        t.hide_apps = True
        update = [x for x in self.log if x[0].startswith("WITH RECURSIVE")][-1]
        self.assertEqual(update[1], (False,))         # hidden -> active = False

    def test_toggle_on_a_draft_tenant_touches_no_database(self):
        t = self.Tenant.create({"name": "draftcust"})
        t.hide_apps = False
        self.assertEqual(self.log, [])

    def test_sync_users_reads_the_count(self):
        t = self.Tenant.create({"name": "usersync", "state": "active"})
        t.with_user(self._dev()).action_sync_users()
        self.assertEqual(t.user_count, 7)
        self.assertTrue(t.users_synced_at)

    def test_sync_users_refuses_an_inactive_tenant(self):
        t = self.Tenant.create({"name": "notactiv"})
        with self.assertRaises(UserError):
            t.with_user(self._dev()).action_sync_users()
