import logging
import os
import re
import secrets
import shutil
import subprocess
import string
from contextlib import closing
from datetime import timedelta

import psycopg2

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.service import db as odoo_db

_logger = logging.getLogger(__name__)

# A tenant name is both a PostgreSQL database name and a DNS label, so it has to
# satisfy the stricter of the two. Leading digits are excluded because an
# unquoted identifier starting with a digit is a nuisance in psql sessions.
NAME_RE = re.compile(r"^[a-z][a-z0-9]{2,30}$")

# Names that would collide with PostgreSQL, with the control-plane database, or
# with a hostname the reverse proxy already answers on. create_tenant.sh checked
# none of these.
RESERVED_NAMES = frozenset({
    "postgres", "template0", "template1", "odoo", "root",
    "www", "mail", "smtp", "imap", "webmail", "ns1", "ns2",
    "api", "app", "cdn", "static", "assets",
    "admin", "manage", "saas", "msolutions",
})

PASSWORD_ALPHABET = string.ascii_letters + string.digits


class SaasTenant(models.Model):
    _name = "saas.tenant"
    _description = "SaaS Tenant"
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Tenant Name",
        required=True,
        index=True,
        copy=False,
        help="Database name and subdomain. Lowercase letters and digits, "
             "starting with a letter.",
    )
    company_name = fields.Char(
        string="Company",
        help="Free-text label. Does not affect provisioning.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("provisioning", "Provisioning"),
            ("active", "Active"),
            ("error", "Error"),
            ("terminating", "Terminating"),
            ("terminated", "Terminated"),
        ],
        default="draft",
        required=True,
        copy=False,
        index=True,
    )
    admin_login = fields.Char(default="admin", required=True, copy=False)
    admin_password = fields.Char(
        copy=False,
        help="Generated on provisioning. Shown so it can be handed to the "
             "customer once; change it after first login.",
    )
    url = fields.Char(compute="_compute_url")
    error_message = fields.Text(readonly=True, copy=False)

    # When the tenant entered its current transient state. Set on the way into
    # 'provisioning'/'terminating'; the watchdog uses it to time out a run that
    # never finished (a worker that died mid-provision would otherwise leave the
    # record stuck forever).
    state_since = fields.Datetime(string="In State Since", readonly=True, copy=False)
    # Set once the tenant has fully provisioned at least once. Retry refuses to
    # re-provision an ever-active tenant, because that drops the database and a
    # live tenant holds real customer data.
    ever_active = fields.Boolean(readonly=True, copy=False, default=False)

    # Odoo 19 dropped _sql_constraints -- it is now ignored with a log warning.
    _name_uniq = models.Constraint(
        "UNIQUE (name)",
        "A tenant with that name already exists.",
    )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @api.model
    def _base_domain(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "msolutions_saas.base_domain", "msolutions-eg.com"
        )

    @api.model
    def _base_scheme(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "msolutions_saas.base_scheme", "https"
        )

    @api.depends("name")
    def _compute_url(self):
        """Build the tenant URL from configuration, not from a constant.

        A tenant provisioned by a development instance lives in that instance's
        database cluster, not in production -- so hardcoding the production
        domain here makes a local tenant advertise a URL that 404s on the real
        server. Both halves are config parameters so each environment tells the
        truth about itself.
        """
        domain = self._base_domain()
        scheme = self._base_scheme()
        for tenant in self:
            tenant.url = f"{scheme}://{tenant.name}.{domain}" if tenant.name else False

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @api.constrains("name")
    def _check_name(self):
        for tenant in self:
            name = tenant.name or ""
            if not NAME_RE.match(name):
                raise ValidationError(_(
                    "'%(name)s' is not a usable tenant name.\n\n"
                    "Use 3 to 31 characters: lowercase letters and digits only, "
                    "starting with a letter.",
                    name=name,
                ))
            if name in RESERVED_NAMES:
                raise ValidationError(_(
                    "'%(name)s' is reserved and cannot be used as a tenant name.",
                    name=name,
                ))

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Security helpers
    # ------------------------------------------------------------------

    @api.model
    def _check_developer_group(self):
        """Raise if the calling user is not in group_saas_developer.

        Called at the entry of every @api.model method reachable over RPC.
        The access CSV and ir.rule are the primary gates; this is the third
        layer so that a CSV bypass (e.g. a sudo() in a third-party module
        that touches this model) does not silently give a plain user access.
        """
        if not self.env.user.has_group("msolutions_saas.group_saas_developer"):
            raise UserError(_(
                "Access denied: the SaaS Developer role is required.",
            ))

    def _log(self, action, detail=None):
        """Write one immutable audit entry for this tenant.

        Uses sudo() so cron calls (uid=1) write the log without needing the
        developer group. user_id is captured from the *original* env uid
        before sudo() replaces it with the superuser, so user-triggered
        actions record the authenticated user, not uid=1.
        """
        self.ensure_one()
        self.env["saas.audit.log"].sudo().create({
            "tenant_id": self.id,
            "tenant_name": self.name,
            "user_id": self.env.uid,
            "action": action,
            "detail": detail or False,
        })

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def action_provision(self):
        """Queue the tenant for creation. The cron does the work."""
        self._check_developer_group()
        self._assert_control_plane()
        for tenant in self:
            if tenant.state not in ("draft", "error"):
                raise UserError(_(
                    "Tenant %(name)s is %(state)s and cannot be provisioned.",
                    name=tenant.name, state=tenant.state,
                ))
            if tenant._database_exists():
                raise UserError(_(
                    "A database named %(name)s already exists on the server. "
                    "Pick another name, or drop that database first.",
                    name=tenant.name,
                ))
        self.write({
            "state": "provisioning",
            "error_message": False,
            "state_since": fields.Datetime.now(),
        })
        self._trigger_cron()
        for tenant in self:
            tenant._log("provision_queued")
        return True

    def action_drop(self):
        """Queue the tenant for deletion. The cron does the work.

        The confirmation lives in the client action; by the time this runs the
        user has already typed the tenant name back.
        """
        self._check_developer_group()
        self._assert_control_plane()
        for tenant in self:
            if tenant.state not in ("active", "error"):
                raise UserError(_(
                    "Tenant %(name)s is %(state)s and cannot be dropped.",
                    name=tenant.name, state=tenant.state,
                ))
        self.write({
            "state": "terminating",
            "error_message": False,
            "state_since": fields.Datetime.now(),
        })
        self._trigger_cron()
        for tenant in self:
            tenant._log("drop_queued")
        return True

    def action_retry(self):
        """Re-queue a failed provision, after the worker cleans up its remnant.

        The cleanup is a DROP of the half-created database, but it does NOT
        happen here: this action is reachable from the web tier, which connects
        as odoo_web and has no privilege to drop a database. So retry only flips
        the record back to 'provisioning'; _run_provision (in the worker, as
        odoo_provision) drops the leftover before recreating -- otherwise the
        retry fails with "database already exists".

        Retry is refused for an ever-active tenant: re-provisioning drops the
        database, and a tenant that was ever live holds real customer data.
        """
        self._check_developer_group()
        self._assert_control_plane()
        self.ensure_one()
        if self.state != "error":
            raise UserError(_(
                "Tenant %(name)s is %(state)s; only a tenant in error can be "
                "retried.", name=self.name, state=self.state,
            ))
        if self.ever_active:
            raise UserError(_(
                "Tenant %(name)s was live and holds customer data. Retry would "
                "re-provision it from scratch, destroying that data. If you "
                "really mean to destroy it, use Drop instead.",
                name=self.name,
            ))
        self.write({
            "state": "provisioning",
            "error_message": False,
            "state_since": fields.Datetime.now(),
        })
        self._trigger_cron()
        self._log("retry_queued")
        return True

    def action_open_url(self):
        self._check_developer_group()
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.url, "target": "new"}

    def _assert_control_plane(self):
        """Refuse to run anywhere but the designated control-plane database.

        The module sits on the addons_path of the single odoo_web container,
        which serves every tenant, so it also shows up in each tenant's Apps
        list. Installing it there must not yield a working tenant-provisioning
        console.

        The designated name is read from odoo.conf -- NOT from
        ir_config_parameter -- because odoo.conf is mounted read-only into the
        container and is the same file for every database, so a tenant cannot
        edit it to nominate itself. Set it in /opt/odoo/config/odoo.conf:

            saas_control_db = manage

        When it is unset the guard stays open, so development instances and
        the existing local setup keep working unchanged.
        """
        configured = odoo.tools.config.get("saas_control_db")
        if configured and self.env.cr.dbname != configured:
            raise UserError(_(
                "This database (%(current)s) is not the SaaS control plane. "
                "Tenant provisioning is only available in %(expected)s.",
                current=self.env.cr.dbname, expected=configured,
            ))

    def _trigger_cron(self):
        cron = self.env.ref("msolutions_saas.cron_process_tenants", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_process(self):
        """Handle one tenant per run.

        One at a time on purpose: installing ``base`` pins a CPU for about a
        minute, and there is a single cron thread. Whatever is left over is
        picked up by the next run.
        """
        self._assert_control_plane()
        tenant = self._claim_one("provisioning")
        if tenant:
            tenant._run_provision()
            return

        tenant = self._claim_one("terminating")
        if tenant:
            tenant._run_drop()

    def _claim_one(self, state):
        """Claim a single tenant in ``state`` for this cron run.

        SELECT ... FOR UPDATE SKIP LOCKED locks the row until the transaction
        commits (which _run_provision/_run_drop do at the end), so a second
        cron worker running at the same time skips it instead of double-claiming
        the same tenant and racing on the same database. max_cron_threads is 1
        today, but this keeps the invariant if that ever changes.
        """
        self.env.cr.execute(
            "SELECT id FROM saas_tenant WHERE state = %s "
            "ORDER BY create_date, id FOR UPDATE SKIP LOCKED LIMIT 1",
            (state,),
        )
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    @api.model
    def _cron_watchdog(self):
        """Rescue tenants stuck in a transient state.

        Provisioning and terminating take about a minute. If the worker dies
        mid-run the record sits in 'provisioning'/'terminating' forever with no
        one to finish it (this happened: a tenant sat in 'provisioning' for over
        two hours). After a configurable timeout, move it to 'error' so the
        operator sees it and can Retry. Runs in the worker like every cron.
        """
        self._assert_control_plane()
        timeout = int(self.env["ir.config_parameter"].sudo().get_param(
            "msolutions_saas.stuck_timeout_minutes", 15))
        deadline = fields.Datetime.now() - timedelta(minutes=timeout)
        stuck = self.search([
            ("state", "in", ("provisioning", "terminating")),
            ("state_since", "<", deadline),
        ])
        for tenant in stuck:
            _logger.warning(
                "SaaS: watchdog timing out tenant %s (stuck in '%s' since %s)",
                tenant.name, tenant.state, tenant.state_since,
            )
            tenant.write({
                "state": "error",
                "error_message": _(
                    "Timed out in '%(prev)s' after %(mins)s minutes -- the "
                    "worker likely died mid-run. Use Retry to clean up and try "
                    "again.",
                    prev=tenant.state, mins=timeout,
                ),
            })
            # Commit per tenant so a failure on a later one does not roll back
            # the rescue of the earlier ones.
            self.env.cr.commit()

    def _run_provision(self):
        self.ensure_one()
        _logger.info("SaaS: provisioning tenant %s", self.name)
        try:
            if self._database_exists():
                # Retry path: a previous failed attempt left a half-created
                # database. action_retry guarantees this record was never active
                # (so there is no customer data), and action_provision refuses a
                # name whose database already exists -- so an existing database
                # here is always our own remnant. Drop it, or _create_empty_
                # database fails with "database already exists".
                _logger.warning(
                    "SaaS: dropping half-provisioned database %s before retry",
                    self.name,
                )
                self._drop_database_raw()
            self._provision_database()
            self._provision_odoo()
            self._grant_web_ownership()
            self.write({
                "state": "active",
                "ever_active": True,
                "error_message": False,
            })
            self._log("provision_ok")
            _logger.info("SaaS: tenant %s is active", self.name)
        except Exception as exc:  # noqa: BLE001 - recorded on the record
            _logger.exception("SaaS: provisioning tenant %s failed", self.name)
            self.write({"state": "error", "error_message": str(exc)})
            self._log("provision_failed", detail=str(exc)[:2000])
        # The database was created outside this transaction, so the record must
        # be committed even on failure -- otherwise a rollback loses the only
        # pointer to a database that now exists.
        self.env.cr.commit()

    def _run_drop(self):
        self.ensure_one()
        _logger.info("SaaS: dropping tenant %s", self.name)
        try:
            self._drop_database()
            self.write({"state": "terminated", "error_message": False})
            self._log("drop_ok")
            _logger.info("SaaS: tenant %s terminated", self.name)
        except Exception as exc:  # noqa: BLE001 - recorded on the record
            _logger.exception("SaaS: dropping tenant %s failed", self.name)
            self.write({"state": "error", "error_message": str(exc)})
            self._log("drop_failed", detail=str(exc)[:2000])
        self.env.cr.commit()

    # ------------------------------------------------------------------
    # Provisioning steps
    #
    # These are the seams. Moving off Contabo means overriding the step that
    # actually changes -- most likely only _provision_database, if Postgres
    # becomes a managed service -- in a module that inherits saas.tenant.
    # ------------------------------------------------------------------

    def _database_exists(self):
        self.ensure_one()
        connection = odoo.sql_db.db_connect("postgres")
        with closing(connection.cursor()) as cr:
            cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.name,))
            return bool(cr.fetchone())

    def _provision_database(self):
        """Create the empty database.

        Uses Odoo's own helper rather than a psql subprocess: it quotes the
        identifier, picks the collation to match db_template, and installs
        pg_trgm/unaccent. Note it is _create_empty_database, not
        exp_create_database -- the exp_* wrappers are gated on list_db, which is
        False here (and should stay False).
        """
        self.ensure_one()
        odoo_db._create_empty_database(self.name)

    def _provision_odoo(self):
        """Install ``base`` and set the admin credentials."""
        self.ensure_one()
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _i in range(16))
        odoo_db._initialize_db(
            self.name,
            demo=False,
            lang="en_US",
            user_password=password,
            login=self.admin_login,
        )
        # _initialize_db logs its exceptions and returns normally, so success
        # has to be confirmed rather than assumed.
        self._assert_initialized()
        self.admin_password = password

    def _assert_initialized(self):
        self.ensure_one()
        try:
            connection = odoo.sql_db.db_connect(self.name)
            with closing(connection.cursor()) as cr:
                cr.execute("SELECT state FROM ir_module_module WHERE name = 'base'")
                row = cr.fetchone()
        except psycopg2.Error as exc:
            raise UserError(_(
                "Database %(name)s was created but Odoo did not initialise it. "
                "See the server log for the traceback.\n\n%(error)s",
                name=self.name, error=exc,
            )) from exc
        if not row or row[0] != "installed":
            raise UserError(_(
                "Database %(name)s was created but the 'base' module is not "
                "installed. See the server log for the traceback.",
                name=self.name,
            ))


    # The worker connects as odoo_provision (CREATEDB); it creates and owns the
    # tenant database and every object base installs. The web/gevent tiers
    # connect as odoo_web (NOSUPERUSER, NOCREATEDB), which must run DML/DDL on
    # those objects but must NOT be able to drop the database. So: hand object
    # ownership to odoo_web, while the DATABASE stays owned by odoo_provision.
    # odoo_provision is a member of odoo_web, so it is allowed to reassign to it.
    _GRANT_WEB_OWNERSHIP_SQL = """
        DO $r$
        DECLARE x record;
        BEGIN
          FOR x IN SELECT nspname FROM pg_namespace
                   WHERE nspname NOT LIKE 'pg\\_%' AND nspname<>'information_schema'
                     AND pg_get_userbyid(nspowner)='odoo_provision'
          LOOP EXECUTE format('ALTER SCHEMA %I OWNER TO odoo_web', x.nspname); END LOOP;
          FOR x IN SELECT n.nspname ns, c.relname rel, c.relkind kind
                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname<>'information_schema'
                     AND c.relkind IN ('r','p','S','v','m','f')
                     AND pg_get_userbyid(c.relowner)='odoo_provision'
                     AND NOT (c.relkind='S' AND EXISTS (SELECT 1 FROM pg_depend d
                              WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype IN ('a','i')))
          LOOP EXECUTE format('ALTER %s %I.%I OWNER TO odoo_web',
                 CASE x.kind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END, x.ns, x.rel);
          END LOOP;
          FOR x IN SELECT p.oid::regprocedure sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname<>'information_schema'
                     AND pg_get_userbyid(p.proowner)='odoo_provision'
          LOOP EXECUTE format('ALTER ROUTINE %s OWNER TO odoo_web', x.sig); END LOOP;
        END $r$;
        ALTER SCHEMA public OWNER TO odoo_web;
    """

    def _grant_web_ownership(self):
        """Give odoo_web ownership of the new tenant's schema objects."""
        self.ensure_one()
        connection = odoo.sql_db.db_connect(self.name)
        with closing(connection.cursor()) as cr:
            cr.execute(self._GRANT_WEB_OWNERSHIP_SQL)
            cr.execute(
                odoo.tools.SQL("GRANT CONNECT ON DATABASE %s TO odoo_web",
                               odoo.tools.SQL.identifier(self.name))
            )
            cr._cnx.commit()
        _logger.info("SaaS: granted odoo_web ownership on tenant %s", self.name)


    def _backup_before_drop(self):
        """Take a full DB+filestore backup BEFORE dropping a tenant, using
        pg_dump directly (Odoo's dump_db is blocked when list_db=False). Raises
        on any failure so the drop is aborted -- a customer database must never
        be destroyed without a recoverable copy. Written to the data_dir (on the
        Odoo host, a different machine than Postgres); the daily job sweeps it to B2."""
        self.ensure_one()
        cfg = odoo.tools.config
        backup_dir = os.path.join(cfg["data_dir"], "pre_drop_backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = fields.Datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(backup_dir, "%s_%s" % (self.name, ts))
        sql_path = base + ".sql"
        fs_path = base + ".filestore.tar.gz"

        env = dict(os.environ)
        if cfg.get("db_password"):
            env["PGPASSWORD"] = cfg["db_password"]
        if cfg.get("db_sslmode"):
            env["PGSSLMODE"] = cfg["db_sslmode"]
        args = ["pg_dump", "--no-owner", "-Fp", "--file=" + sql_path]
        if cfg.get("db_host"):
            args += ["--host", cfg["db_host"]]
        if cfg.get("db_port"):
            args += ["--port", str(cfg["db_port"])]
        if cfg.get("db_user"):
            args += ["--username", cfg["db_user"]]
        args.append(self.name)

        try:
            subprocess.run(args, env=env, check=True, capture_output=True, timeout=1800)
            filestore = cfg.filestore(self.name)
            if os.path.exists(filestore):
                subprocess.run(
                    ["tar", "czf", fs_path, "-C", os.path.dirname(filestore),
                     os.path.basename(filestore)],
                    check=True, capture_output=True, timeout=1800,
                )
        except Exception as exc:  # noqa: BLE001
            for f in (sql_path, fs_path):
                if os.path.exists(f):
                    os.remove(f)
            detail = getattr(exc, "stderr", b"")
            detail = detail.decode(errors="replace") if isinstance(detail, bytes) else str(exc)
            raise UserError(_(
                "Refusing to drop %(name)s: the safety backup failed "
                "(%(err)s). No data was deleted.",
                name=self.name, err=detail[:300] or str(exc),
            )) from exc

        size = os.path.getsize(sql_path)
        if size < 4096:
            os.remove(sql_path)
            raise UserError(_(
                "Refusing to drop %(name)s: safety backup is implausibly small "
                "(%(size)d bytes). No data was deleted.",
                name=self.name, size=size,
            ))
        _logger.info("SaaS: pre-drop backup %s (%d bytes) + filestore", sql_path, size)
        return sql_path

    def _drop_database(self):
        """Drop a live tenant's database and filestore, after a safety backup."""
        self.ensure_one()
        if not self._database_exists():
            _logger.info("SaaS: database %s already gone", self.name)
            return
        self._backup_before_drop()
        self._drop_database_raw()

    def _drop_database_raw(self):
        """Tear down the registry, DROP DATABASE, and remove the filestore.

        No safety backup here -- the caller decides. _drop_database takes one
        first (a live tenant holds real data); the retry cleanup of a
        half-created database does not, because a never-active tenant has
        nothing to lose. The filestore removal is the part delete_tenant.sh
        missed -- there are orphaned directories under the data_dir from tenants
        dropped earlier.
        """
        self.ensure_one()
        odoo.modules.registry.Registry.delete(self.name)
        odoo.sql_db.close_db(self.name)

        connection = odoo.sql_db.db_connect("postgres")
        with closing(connection.cursor()) as cr:
            cr._cnx.autocommit = True
            odoo_db._drop_conn(cr, self.name)
            cr.execute(
                odoo.tools.SQL("DROP DATABASE %s", odoo_db.database_identifier(cr, self.name))
            )

        filestore = odoo.tools.config.filestore(self.name)
        if os.path.exists(filestore):
            shutil.rmtree(filestore, ignore_errors=True)


    # ------------------------------------------------------------------
    # Client action data
    # ------------------------------------------------------------------

    @api.model
    def dashboard_data(self):
        """Everything the dashboard needs, in one call."""
        tenants = self.search([])
        return {
            "base_domain": self._base_domain(),
            "tenants": [
                {
                    "id": t.id,
                    "name": t.name,
                    "company_name": t.company_name or "",
                    "state": t.state,
                    "url": t.url,
                    "admin_login": t.admin_login,
                    "admin_password": t.admin_password or "",
                    "error_message": t.error_message or "",
                }
                for t in tenants
            ],
        }

    @api.model
    def create_tenant(self, name, company_name=None):
        """Create the record and queue it, in one call from the dashboard."""
        tenant = self.create({
            "name": (name or "").strip().lower(),
            "company_name": company_name or False,
        })
        tenant.action_provision()
        return tenant.id
