from datetime import timedelta
from unittest.mock import patch

from odoo import fields
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

    def test_dashboard_counters(self):
        self._trial("t1", days=10)
        self._trial("t2", days=2)             # ending soon
        done = self._trial("t3", days=-1)
        done.trial_expired = True
        self.Tenant.create({"name": "a1", "state": "active"})
        group = self.env.ref("msolutions_saas.group_saas_developer")
        user = self.env["res.users"].create({"name": "Dev", "login": "dev_trial@test.com"})
        user.write({"group_ids": [(4, group.id)]})
        d = self.Tenant.with_user(user).simple_dashboard_data()
        base = {"trial": 2, "trial_ending": 1, "trial_expired": 1}
        for k, v in base.items():
            self.assertGreaterEqual(d[k], v, k)
        self.assertGreaterEqual(d["active"], 1)
