from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase


class TestSaasSecurity(TransactionCase):
    """Section 2: security group + audit log.

    These tests verify three things:
      1. Non-developer users are refused at every RPC-reachable method.
      2. dashboard_data never returns a password more than once.
      3. The audit log is immutable after creation.

    Psycopg side-effects (_database_exists, _trigger_cron, etc.) are patched
    out for the same reasons as test_provisioning.py: these tests exercise the
    control-plane logic, not the Postgres cluster.
    """

    def setUp(self):
        super().setUp()
        self.Tenant = self.env["saas.tenant"]
        self.AuditLog = self.env["saas.audit.log"]

        # Neutralise the control-plane guard for all tests in this class.
        p = patch.object(type(self.Tenant), "_assert_control_plane",
                         lambda self: None)
        p.start()
        self.addCleanup(p.stop)

        dev_group = self.env.ref("msolutions_saas.group_saas_developer")
        user_group = self.env.ref("base.group_user")

        self.plain_user = self.env["res.users"].create({
            "name": "Plain User",
            "login": "plain_sec@test.com",
            "groups_id": [(6, 0, [user_group.id])],
        })
        self.dev_user = self.env["res.users"].create({
            "name": "Developer User",
            "login": "dev_sec@test.com",
            "groups_id": [(6, 0, [user_group.id, dev_group.id])],
        })

    # ------------------------------------------------------------------
    # Access control — @api.model RPC methods
    # ------------------------------------------------------------------

    def test_non_developer_refused_dashboard_data(self):
        """dashboard_data must raise for a user not in group_saas_developer.

        This method is reachable by any authenticated user over JSON-RPC
        without needing to hold a record ID, so the group check in the method
        body is the primary guard here (layer 3 of 3).
        """
        with self.assertRaises(UserError):
            self.Tenant.with_user(self.plain_user).dashboard_data()

    def test_non_developer_refused_create_tenant(self):
        """create_tenant must raise for a user not in group_saas_developer.

        Same reasoning as dashboard_data: callable without a record ID.
        """
        with self.assertRaises(UserError):
            self.Tenant.with_user(self.plain_user).create_tenant("acmesec")

    def test_non_developer_cannot_read_tenant_records(self):
        """ORM layer (CSV perm_read=0) blocks non-developers before Python code runs."""
        self.Tenant.create({"name": "readtest"})
        with self.assertRaises(AccessError):
            self.Tenant.with_user(self.plain_user).search([])

    def test_non_developer_cannot_read_audit_log(self):
        """Audit log is readable only by group_saas_developer (CSV perm_read=0 for all others)."""
        with self.assertRaises(AccessError):
            self.AuditLog.with_user(self.plain_user).search([])

    # ------------------------------------------------------------------
    # Password clearing
    # ------------------------------------------------------------------

    def _dashboard_as_dev(self):
        """Call dashboard_data as the dev user, with control-plane guard off."""
        # The with_user() copy needs the guard patched too; the class-level
        # patch.object covers all instances of the same model class.
        return self.Tenant.with_user(self.dev_user).dashboard_data()

    def test_dashboard_data_returns_password_once(self):
        """Password is included in the first dashboard_data response after provisioning.

        Simulates what _run_provision does: it writes admin_password to the
        record. dashboard_data should pick that up and return it.
        """
        t = self.Tenant.create({"name": "pwonce"})
        t.write({"admin_password": "s3cr3t", "state": "active"})

        data = self._dashboard_as_dev()

        entry = next(x for x in data["tenants"] if x["id"] == t.id)
        self.assertEqual(entry["admin_password"], "s3cr3t",
                         "Password must be present in the first call")

    def test_dashboard_data_clears_password_from_record(self):
        """After dashboard_data is called, admin_password is wiped from the record.

        The field must not persist so a later caller, a log export, or a
        database snapshot does not expose it.
        """
        t = self.Tenant.create({"name": "pwclear"})
        t.write({"admin_password": "s3cr3t", "state": "active"})

        self._dashboard_as_dev()

        # Invalidate the ORM cache so we read back from the database.
        t.invalidate_recordset()
        self.assertFalse(t.admin_password,
                         "admin_password must be cleared from the DB after being shown")

    def test_dashboard_data_empty_on_second_call(self):
        """On the second call, password is already gone and the field is empty."""
        t = self.Tenant.create({"name": "pwsecond"})
        t.write({"admin_password": "s3cr3t", "state": "active"})

        self._dashboard_as_dev()          # first call: clears it
        data = self._dashboard_as_dev()   # second call: nothing to return

        entry = next(x for x in data["tenants"] if x["id"] == t.id)
        self.assertFalse(entry["admin_password"],
                         "Password must be empty on the second dashboard_data call")

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def test_audit_log_written_on_provision_queued(self):
        """action_provision must produce an audit entry with action='provision_queued'.

        Mocks: _trigger_cron (no cron scheduler in tests), _database_exists
        (no Postgres cluster in tests). Both are stand-ins for real side effects.
        """
        t = self.Tenant.create({"name": "auditprov"})
        before = self.AuditLog.search_count([("tenant_id", "=", t.id)])

        with patch.object(type(t), "_trigger_cron", lambda self: None), \
             patch.object(type(t), "_database_exists", lambda self: False):
            t.with_user(self.dev_user).action_provision()

        logs = self.AuditLog.search([
            ("tenant_id", "=", t.id),
            ("action", "=", "provision_queued"),
        ])
        self.assertEqual(len(logs), before + 1)
        self.assertEqual(logs[0].user_id.id, self.dev_user.id,
                         "Audit log must record the authenticated user, not uid=1")

    def test_audit_log_written_on_drop_queued(self):
        """action_drop must produce an audit entry with action='drop_queued'."""
        t = self.Tenant.create({"name": "auditdrop"})
        t.write({"state": "active", "ever_active": True})

        with patch.object(type(t), "_trigger_cron", lambda self: None):
            t.with_user(self.dev_user).action_drop()

        logs = self.AuditLog.search([
            ("tenant_id", "=", t.id),
            ("action", "=", "drop_queued"),
        ])
        self.assertTrue(logs, "drop_queued audit entry must exist after action_drop")

    def test_audit_log_written_on_retry(self):
        """action_retry must produce an audit entry with action='retry_queued'."""
        t = self.Tenant.create({"name": "auditretry"})
        t.write({"state": "error", "ever_active": False})

        with patch.object(type(t), "_trigger_cron", lambda self: None):
            t.with_user(self.dev_user).action_retry()

        logs = self.AuditLog.search([
            ("tenant_id", "=", t.id),
            ("action", "=", "retry_queued"),
        ])
        self.assertTrue(logs)

    def test_audit_log_written_on_provision_ok(self):
        """_run_provision must write a 'provision_ok' entry on success.

        Mocks all Postgres side-effects; the commit is neutralised so the
        test transaction stays intact.
        """
        t = self.Tenant.create({"name": "auditok"})
        t.write({"state": "provisioning"})

        with patch.object(type(t), "_database_exists", lambda self: False), \
             patch.object(type(t), "_provision_database", lambda self: None), \
             patch.object(type(t), "_provision_odoo", lambda self: None), \
             patch.object(type(t), "_grant_web_ownership", lambda self: None), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()

        logs = self.AuditLog.search([
            ("tenant_id", "=", t.id),
            ("action", "=", "provision_ok"),
        ])
        self.assertTrue(logs, "provision_ok audit entry must exist after successful _run_provision")

    def test_audit_log_written_on_provision_failed(self):
        """_run_provision must write a 'provision_failed' entry when an exception occurs."""
        t = self.Tenant.create({"name": "auditfail"})
        t.write({"state": "provisioning"})

        def _boom(self):
            raise RuntimeError("disk full")

        with patch.object(type(t), "_database_exists", lambda self: False), \
             patch.object(type(t), "_provision_database", _boom), \
             patch.object(self.env.cr, "commit", lambda: None):
            t._run_provision()

        logs = self.AuditLog.search([
            ("tenant_id", "=", t.id),
            ("action", "=", "provision_failed"),
        ])
        self.assertTrue(logs)
        self.assertIn("disk full", logs[0].detail)

    # ------------------------------------------------------------------
    # Immutability
    # ------------------------------------------------------------------

    def test_audit_log_write_raises(self):
        """write() on any audit log entry must raise UserError regardless of caller.

        This check matters for sudo() callers: the CSV (perm_write=0) is
        bypassed by sudo(), so the model-level override is the actual guard.
        """
        t = self.Tenant.create({"name": "immutable"})
        log = self.AuditLog.sudo().create({
            "tenant_id": t.id,
            "tenant_name": t.name,
            "action": "provision_queued",
        })
        with self.assertRaises(UserError):
            log.write({"detail": "tampered"})

    def test_audit_log_unlink_raises(self):
        """unlink() on any audit log entry must raise UserError regardless of caller."""
        t = self.Tenant.create({"name": "nodelete"})
        log = self.AuditLog.sudo().create({
            "tenant_id": t.id,
            "tenant_name": t.name,
            "action": "drop_queued",
        })
        with self.assertRaises(UserError):
            log.unlink()

    def test_audit_log_tenant_name_snapshot_survives_tenant(self):
        """tenant_name is stored at log time; the entry stays readable after the tenant goes.

        ondelete='set null' on tenant_id means the FK goes null, but
        tenant_name is a plain Char that survives.
        """
        t = self.Tenant.create({"name": "goingaway"})
        log = self.AuditLog.sudo().create({
            "tenant_id": t.id,
            "tenant_name": t.name,
            "action": "provision_queued",
        })
        captured_name = t.name
        # Bypass our unlink guard: call the ORM unlink directly on saas.tenant,
        # not on the audit log. saas.tenant.unlink() is not overridden.
        t.unlink()
        log.invalidate_recordset()
        self.assertFalse(log.tenant_id,
                         "tenant_id FK must be null after tenant deletion")
        self.assertEqual(log.tenant_name, captured_name,
                         "tenant_name snapshot must survive tenant deletion")
