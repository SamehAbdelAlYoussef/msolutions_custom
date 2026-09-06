import logging
import os
import re
import secrets
import shutil
import subprocess
import string
import sys
import uuid
from contextlib import closing
from datetime import timedelta

import psycopg2
from passlib.context import CryptContext

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.service import db as odoo_db

# Odoo hashes passwords with pbkdf2_sha512; a hash we generate with the same
# scheme verifies against res.users.login unchanged. Used only on the template
# path, where the admin password is set by raw SQL (no tenant registry is
# opened) -- the from-scratch path gets its password from _initialize_db.
_CRYPT_CONTEXT = CryptContext(schemes=["pbkdf2_sha512"])

_logger = logging.getLogger(__name__)


def _serialize_row(row):
    """Convert a psycopg row to a JSON-safe list."""
    import datetime
    from decimal import Decimal
    import uuid as _uuid
    out = []
    for v in row:
        if v is None:
            out.append(None)
        elif isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
            out.append(str(v))
        elif isinstance(v, Decimal):
            out.append(float(v))
        elif isinstance(v, _uuid.UUID):
            out.append(str(v))
        elif isinstance(v, memoryview):
            out.append(f"<binary {len(v)} B>")
        elif isinstance(v, (list, dict, tuple)):
            out.append(str(v))
        else:
            out.append(v)
    return out

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

    # Apps to install right after base is initialised. Defaults to the global
    # SaaS configuration so every new tenant starts with the same stack, but
    # can be trimmed or extended per tenant before provisioning.
    module_ids = fields.Many2many(
        "ir.module.module",
        "saas_tenant_module_rel",
        "tenant_id",
        "module_id",
        string="Modules to Install",
        domain="[]",
        default=lambda self: self.env["saas.config"]._get().default_module_ids,
        help="Installed automatically when the tenant is provisioned. "
             "Edit before clicking Create; cannot be changed after provisioning.",
    )

    # When set, provisioning clones the plan's template database (seconds)
    # instead of building the schema from scratch (minutes). If the plan has no
    # template, or the template is missing, provisioning falls back to a
    # from-scratch build -- a missing template never breaks a tenant.
    plan_id = fields.Many2one(
        "saas.plan",
        string="Plan",
        ondelete="restrict",
        copy=False,
        default=lambda self: self.env["saas.config"]._get().default_plan_id,
        help="Provision by cloning this plan's template database. Defaults to "
             "the configured Default Plan, so tenants created from the "
             "dashboard clone in seconds. Without a plan (or if its template is "
             "missing) the tenant is built from scratch.",
    )

    # Soft storage allowance for this tenant, in GB. Drives the used/quota gauge
    # and the near-full/full upsell signal on the dashboard. Not enforced by
    # Postgres -- it is a billing/monitoring quota, not a hard write block.
    quota_gb = fields.Float(
        string="Storage Quota (GB)",
        default=lambda self: self.env["saas.config"]._get().default_quota_gb,
        help="Storage allowance for this tenant. When usage nears it, that is "
             "the signal to sell an upgrade (raise this number).",
    )
    # Set by the quota enforcer when usage exceeds quota. While True the reverse
    # proxy serves the 'storage limit reached' page instead of the tenant, so
    # the customer is locked out until the quota is raised. The tenant database
    # is untouched -- lifting this restores access immediately.
    suspended = fields.Boolean(
        string="Suspended (over quota)", default=False, copy=False,
        help="Auto-set when storage exceeds the quota; the customer sees an "
             "upgrade page until you raise the quota.",
    )

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

    @api.onchange("plan_id")
    def _onchange_plan_id(self):
        """Mirror the plan's modules onto the tenant.

        On the template path the plan's apps are already baked into the clone,
        so this is only cosmetic there; on the fallback (template missing) path
        it is what gets the plan's apps installed from scratch.
        """
        if self.plan_id and self.plan_id.module_ids:
            self.module_ids = self.plan_id.module_ids

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

        # Keep the UI honest: import any tenant database that exists on the
        # server but has no record yet. Wrapped so a discovery failure never
        # stops the watchdog from doing its primary job.
        try:
            if self._discover_tenants():
                self.env.cr.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("SaaS: tenant discovery failed")

    @api.model
    def _discover_tenants(self):
        """Ensure every real tenant database on the server has a record.

        Enumerates tenant databases straight from pg_database -- those owned by
        odoo_provision that are not a template and not the control database --
        and creates an 'active' record for any not yet tracked (e.g. databases
        created before this module, or by the old shell script). Metadata only:
        one query, no per-tenant connection, so it stays cheap at 3 tenants or
        300. Returns the number of newly tracked tenants.
        """
        self._assert_control_plane()
        control = odoo.tools.config.get("saas_control_db") or self.env.cr.dbname
        conn = odoo.sql_db.db_connect("postgres")
        with closing(conn.cursor()) as cr:
            cr.execute(
                "SELECT datname FROM pg_database "
                "WHERE pg_get_userbyid(datdba) = 'odoo_provision' "
                "AND datistemplate = false AND datallowconn = true "
                "AND datname <> %s",
                (control,))
            server_dbs = {r[0] for r in cr.fetchall()}
        known = set(self.with_context(active_test=False).search([]).mapped("name"))
        discovered = 0
        for name in sorted(server_dbs - known):
            # Skip anything that is not a tenant-shaped name (this also excludes
            # tpl_* templates, whose underscore fails NAME_RE) and reserved names.
            if not NAME_RE.match(name) or name in RESERVED_NAMES:
                _logger.warning(
                    "SaaS: server database %s is not importable as a tenant; "
                    "skipping", name)
                continue
            self.create({"name": name, "state": "active", "ever_active": True})
            discovered += 1
            _logger.info("SaaS: discovered untracked tenant database %s", name)
        return discovered

    def action_sync_tenants(self):
        """'Sync from Server' button: import untracked tenant databases now."""
        self._check_developer_group()
        self._assert_control_plane()
        self._discover_tenants()
        # Reopen the list so the newly imported tenants show immediately.
        return self.env["ir.actions.act_window"]._for_xml_id(
            "msolutions_saas.action_saas_tenant_records")

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
            used_template = self._provision_database()
            if used_template:
                # The clone already carries base + the plan's STANDARD apps + an
                # admin user, all copied from the template. It must NOT be
                # initialised again; instead it is scrubbed of the template's
                # identity (uuid, logs, crons, sessions, name, admin password).
                self._post_copy_cleanup()
            else:
                self._provision_odoo()
            # Both paths: install any requested app the database does NOT already
            # have. The template gives the standard apps for free (the clone is
            # instant); anything a tenant wants beyond them is installed here, on
            # demand, and takes its own time. On the from-scratch path only base
            # is present, so this installs the full set. It runs BEFORE
            # _grant_web_ownership: the install subprocess creates objects owned
            # by odoo_provision, and the grant afterwards hands them to odoo_web
            # (granting before caused a 500 -- permission denied on the new
            # tables -- on any tenant with extra modules).
            self._install_extra_modules()
            # Both paths: a clone inherits objects already owned by odoo_web (a
            # no-op re-grant) but still needs the database-level CONNECT, which
            # CREATE DATABASE does not copy. See _grant_web_ownership.
            self._grant_web_ownership()
            self.write({
                "state": "active",
                "ever_active": True,
                "error_message": False,
            })
            self._log("provision_ok",
                      detail="template" if used_template else "scratch")
            _logger.info("SaaS: tenant %s is active (%s)", self.name,
                         "from template" if used_template else "from scratch")
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
        name = self.name
        _logger.info("SaaS: dropping tenant %s", name)
        try:
            self._drop_database()
            # No trace: write the audit entry (which snapshots the name), then
            # remove the RECORD itself -- not just mark it 'terminated'. The
            # database and filestore are already gone; the record was the last
            # pointer to the tenant, so deleting it leaves nothing behind.
            # super().unlink() skips this model's guarded unlink override (which
            # would try to drop the -- now absent -- database again) and the
            # developer-group gate (this is the worker, uid=1). The audit log
            # keeps the history via its tenant_name snapshot.
            self._log("drop_ok")
            super().unlink()
            _logger.info("SaaS: tenant %s dropped; record removed (no trace)", name)
        except Exception as exc:  # noqa: BLE001 - recorded on the record
            _logger.exception("SaaS: dropping tenant %s failed", name)
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
        return self._database_exists_named(self.name)

    def _database_exists_named(self, dbname):
        """True if a database of this name exists. Shared by tenant and template
        existence checks; queries the postgres database, never a registry."""
        connection = odoo.sql_db.db_connect("postgres")
        with closing(connection.cursor()) as cr:
            cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            return bool(cr.fetchone())

    def _template_db(self):
        """The template database to clone, or None to build from scratch.

        A missing template must never break provisioning: if the plan names a
        template that does not exist (never built, or dropped), fall back to a
        from-scratch build rather than failing the tenant.
        """
        self.ensure_one()
        tpl = self.plan_id.template_db if self.plan_id else False
        if not tpl:
            return None
        if not self._database_exists_named(tpl):
            _logger.warning(
                "SaaS: plan %s template %s is missing; building %s from scratch",
                self.plan_id.name, tpl, self.name,
            )
            return None
        return tpl

    def _provision_database(self):
        """Create the tenant database. Return True if a template was cloned.

        Cloning a template copies the schema at file level (seconds) instead of
        building it from scratch (minutes). Falls back to an empty database when
        the plan has no usable template.
        """
        self.ensure_one()
        tpl = self._template_db()
        if tpl:
            _logger.info("SaaS: cloning tenant %s from template %s", self.name, tpl)
            self._provision_from_template(tpl)
            return True
        self._create_empty_database()
        return False

    def _create_empty_database(self):
        """Create the empty database.

        Uses Odoo's own helper rather than a psql subprocess: it quotes the
        identifier, picks the collation to match db_template, and installs
        pg_trgm/unaccent. Note it is _create_empty_database, not
        exp_create_database -- the exp_* wrappers are gated on list_db, which is
        False here (and should stay False).
        """
        self.ensure_one()
        odoo_db._create_empty_database(self.name)

    def _provision_from_template(self, tpl):
        """Clone the tenant database and filestore from a template.

        CREATE DATABASE ... TEMPLATE requires zero other sessions on the
        source; templates are marked ALLOW_CONNECTIONS=false by the rebuild, so
        there are none, but we terminate defensively first in case a template
        was left connectable. The database copy carries only ir_attachment
        *references*, so the template's filestore has to be copied too --
        without it every tenant would point at the template's files and break
        the moment the template is rebuilt or removed.
        """
        self.ensure_one()
        pg = odoo.sql_db.db_connect("postgres")
        with closing(pg.cursor()) as cr:
            cr._cnx.autocommit = True
            cr.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (tpl,))
            cr.execute(odoo.tools.SQL(
                "CREATE DATABASE %s TEMPLATE %s",
                odoo.tools.SQL.identifier(self.name),
                odoo.tools.SQL.identifier(tpl)))
        self._copy_filestore(tpl)

    def _copy_filestore(self, tpl):
        """Copy the template's filestore to the new tenant's own directory."""
        self.ensure_one()
        src = odoo.tools.config.filestore(tpl)
        dst = odoo.tools.config.filestore(self.name)
        if not os.path.isdir(src):
            _logger.info("SaaS: template %s has no filestore; nothing to copy", tpl)
            return
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        _logger.info("SaaS: copied filestore %s -> %s", src, dst)

    def _post_copy_cleanup(self):
        """Strip the template's identity from a freshly cloned tenant.

        A file-level clone is byte-identical to the template, so it shares the
        template's database.uuid, its logs, its cron schedule, its sessions, its
        company name and its admin password. Each of those has to be reset or
        the tenant is not really its own database. Runs as odoo_provision over
        raw psycopg -- the objects are still odoo_provision-owned at this point
        (the ownership hand-off to odoo_web happens next, in
        _grant_web_ownership), and no tenant registry is opened.
        """
        self.ensure_one()
        new_uuid = str(uuid.uuid1())
        # A shared database.secret would let sessions/tokens signed for the
        # template (or a sibling clone) validate here; rotate it too. This is
        # also what makes "clear sessions" real -- every existing signed session
        # becomes invalid the moment the secret changes.
        new_secret = str(uuid.uuid4())
        password = "admin"
        pwd_hash = _CRYPT_CONTEXT.hash(password)
        company = self.company_name or self.name
        login = self.admin_login

        conn = odoo.sql_db.db_connect(self.name)
        with closing(conn.cursor()) as cr:
            # Identity
            cr.execute(
                "UPDATE ir_config_parameter SET value = %s WHERE key = 'database.uuid'",
                (new_uuid,))
            cr.execute(
                "UPDATE ir_config_parameter SET value = %s WHERE key = 'database.secret'",
                (new_secret,))
            # Logs and cron schedule inherited from the template
            cr.execute("TRUNCATE ir_logging")
            cr.execute("UPDATE ir_cron SET nextcall = (now() AT TIME ZONE 'UTC'), "
                       "lastcall = NULL")
            # Sessions / logged-in devices / API keys -- only if the tables are
            # present in this Odoo build. Belt-and-suspenders with the secret
            # rotation above.
            for tbl in ("res_device_log", "res_device", "res_users_apikeys"):
                cr.execute("SELECT to_regclass(%s)", ("public." + tbl,))
                if cr.fetchone()[0] is not None:
                    cr.execute(odoo.tools.SQL(
                        "DELETE FROM %s", odoo.tools.SQL.identifier(tbl)))
            # Company identity (res_company row 1 and its partner)
            cr.execute("UPDATE res_company SET name = %s WHERE id = 1", (company,))
            cr.execute(
                "UPDATE res_partner SET name = %s "
                "WHERE id = (SELECT partner_id FROM res_company WHERE id = 1)",
                (company,))
            # Admin credentials (the base admin user is id 2 in a fresh database)
            cr.execute(
                "UPDATE res_users SET login = %s, password = %s WHERE id = 2",
                (login, pwd_hash))
            cr._cnx.commit()

        self.admin_password = password
        _logger.info(
            "SaaS: post-copy cleanup for %s (new uuid/secret, cron reset, "
            "admin reset)", self.name)

    def _provision_odoo(self):
        """Install ``base`` and set the admin credentials."""
        self.ensure_one()
        # Fixed credentials: password = "admin", login = admin_login (default "admin").
        # The dashboard shows them once so the operator can hand them to the
        # customer; the customer is expected to change the password on first login.
        password = "admin"
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

    def _install_extra_modules(self):
        """Install any requested app the tenant database does not already have.

        The desired set is the tenant's own module_ids (which default to the
        plan's standard apps and can be extended per tenant). Whatever the
        database already has installed -- everything the template baked in, on
        the clone path -- is skipped, so a template clone that wants exactly the
        standard apps installs nothing and stays instant, while a tenant that
        asked for an app beyond the template installs just that delta, on
        demand, and it takes its own time.

        Runs odoo-bin in a subprocess so the tenant registry loads and exits in
        that process, never inside the worker. A failure is logged and recorded
        on the record but does NOT set state='error': the tenant is already
        usable; the operator can install the missing apps manually.
        """
        self.ensure_one()
        # On the fallback path (plan set, template missing) fall back to the
        # plan's modules if the tenant carries none of its own.
        desired = (self.module_ids or self.plan_id.module_ids).mapped("name")
        if not desired:
            return

        # Skip apps the (possibly cloned) database already has -- the template's
        # standard apps, on the clone path.
        conn = odoo.sql_db.db_connect(self.name)
        with closing(conn.cursor()) as cr:
            cr.execute(
                "SELECT name FROM ir_module_module "
                "WHERE state = 'installed' AND name = ANY(%s)", (list(desired),))
            already = {r[0] for r in cr.fetchall()}
        module_names = [n for n in desired if n not in already]
        if not module_names:
            _logger.info(
                "SaaS: tenant %s already has every requested app (from template)",
                self.name)
            return

        _logger.info(
            "SaaS: installing extra modules %s for tenant %s",
            module_names, self.name,
        )
        cfg = odoo.tools.config
        cmd = [
            sys.executable, sys.argv[0],
            "-c", cfg.rcfile,
            "-d", self.name,
            "-i", ",".join(module_names),
            "--stop-after-init",
            "--no-http",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            _logger.info("SaaS: extra modules installed for %s", self.name)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode(errors="replace")
            _logger.error(
                "SaaS: module installation failed for %s: %s",
                self.name, stderr[:500],
            )
            # Keep state='active' — tenant is alive, apps just didn't land.
            self.write({
                "error_message": _(
                    "Tenant is active but app installation failed.\n%(err)s",
                    err=stderr[:400] or str(exc),
                ),
            })

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


    def unlink(self):
        """Delete tenant records, dropping the real database first.

        Keeps the ORM record and the PostgreSQL database in sync so deleting
        from the list view does not leave an orphan database behind.

        Two cases:
          - Database exists: take a safety backup (for ever-active tenants),
            drop the database and filestore, then delete the record.
          - Database already gone (orphan record / failed provision that was
            never initialised): delete the record immediately.

        DROP DATABASE requires the odoo_provision role. On the web tier
        (odoo_web, no CREATEDB/DROP privilege) this will fail if the database
        still exists -- use the Drop button in that case so the worker handles
        it under the correct role. On a single-role local setup it works fine.
        """
        self._check_developer_group()
        self._assert_control_plane()
        for tenant in self:
            if tenant._database_exists():
                try:
                    tenant._drop_database()
                except Exception as exc:
                    raise UserError(_(
                        "Cannot delete '%(name)s': the database exists but "
                        "could not be dropped (%(err)s).\n\n"
                        "Use the Drop button so the worker handles it with "
                        "the correct database role, then delete the record "
                        "once it reaches Terminated.",
                        name=tenant.name, err=str(exc)[:200],
                    )) from exc
            tenant._log("drop_ok")
        return super().unlink()

    # ------------------------------------------------------------------
    # Shell
    # ------------------------------------------------------------------

    def shell_execute(self, query):
        """Execute a SQL statement against the tenant database.

        Restricted to group_saas_developer. Every execution — successful or
        not — is written to the audit log (who ran what, when, on which
        tenant). Statement timeout is 30 s; result set is capped at 500 rows.
        """
        self._check_developer_group()
        self.ensure_one()

        if not query or not query.strip():
            return {"columns": [], "rows": [], "rowcount": 0}

        ROW_LIMIT = 500

        # Log the attempt before running so the trace exists even if the
        # connection itself fails.
        self._log("shell_query", detail=query[:1000])

        try:
            connection = odoo.sql_db.db_connect(self.name)
            with closing(connection.cursor()) as cr:
                cr.execute("SET statement_timeout = '30s'")
                cr.execute(query)
                if cr.description:
                    columns = [d[0] for d in cr.description]
                    rows = cr.fetchmany(ROW_LIMIT)
                    return {
                        "columns": columns,
                        "rows": [_serialize_row(r) for r in rows],
                        "rowcount": cr.rowcount,
                        "truncated": len(rows) == ROW_LIMIT,
                    }
                return {"columns": [], "rows": [], "rowcount": cr.rowcount}
        except Exception as exc:
            raise UserError(str(exc)) from exc

    # ------------------------------------------------------------------
    # DB activity / logs
    # ------------------------------------------------------------------

    def get_db_activity(self):
        """Return live PostgreSQL monitoring data for this tenant.

        Queries pg_stat_activity, pg_stat_database, pg_locks, and
        pg_stat_statements (if the extension is installed) — all via raw
        psycopg to the postgres database, never through the tenant ORM.
        """
        self._check_developer_group()
        self.ensure_one()

        result = {}
        try:
            conn = odoo.sql_db.db_connect("postgres")
            with closing(conn.cursor()) as cr:

                # Active sessions
                cr.execute("""
                    SELECT pid,
                           usename,
                           application_name,
                           state,
                           left(query, 300) AS query,
                           EXTRACT(EPOCH FROM (now() - query_start))::int
                               AS duration_sec,
                           wait_event_type,
                           wait_event
                    FROM pg_stat_activity
                    WHERE datname = %s
                    ORDER BY query_start DESC NULLS LAST
                """, (self.name,))
                result["activity"] = {
                    "columns": [d[0] for d in cr.description],
                    "rows": [list(r) for r in cr.fetchall()],
                }

                # Database-level stats
                cr.execute("""
                    SELECT pg_size_pretty(pg_database_size(%s))  AS db_size,
                           numbackends                             AS connections,
                           xact_commit,
                           xact_rollback,
                           blks_hit,
                           blks_read,
                           CASE WHEN blks_hit + blks_read > 0
                                THEN ROUND(
                                    100.0 * blks_hit / (blks_hit + blks_read), 1)
                                ELSE 0
                           END AS cache_hit_pct
                    FROM pg_stat_database
                    WHERE datname = %s
                """, (self.name, self.name))
                row = cr.fetchone()
                if row:
                    result["stats"] = dict(
                        zip([d[0] for d in cr.description], row)
                    )

                # Blocking locks
                cr.execute("""
                    SELECT pid,
                           usename,
                           pg_blocking_pids(pid) AS blocked_by,
                           left(query, 200)       AS query
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND cardinality(pg_blocking_pids(pid)) > 0
                """, (self.name,))
                result["locks"] = [list(r) for r in cr.fetchall()]

                # pg_stat_statements (optional extension)
                try:
                    cr.execute("""
                        SELECT left(s.query, 200)               AS query,
                               s.calls,
                               ROUND(s.total_exec_time::numeric, 1) AS total_ms,
                               ROUND(s.mean_exec_time::numeric, 1)  AS avg_ms,
                               s.rows
                        FROM pg_stat_statements s
                        JOIN pg_database d ON d.oid = s.dbid
                        WHERE d.datname = %s
                        ORDER BY s.total_exec_time DESC
                        LIMIT 15
                    """, (self.name,))
                    result["slow_queries"] = {
                        "available": True,
                        "columns": [d[0] for d in cr.description],
                        "rows": [list(r) for r in cr.fetchall()],
                    }
                except Exception:
                    result["slow_queries"] = {"available": False}

        except Exception as exc:
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Client action data
    # ------------------------------------------------------------------

    def _disk_usage(self, tenants):
        """Real per-tenant disk usage in bytes: database size + filestore size.

        Database size is pg_database_size (the true on-disk size Postgres
        reports); filestore size is a du over the tenant's filestore directory.
        One query for every database and one du for every filestore, so it stays
        cheap at 3 tenants or 300. Never opens a tenant registry. A missing
        database or filestore reads as 0.
        """
        names = [t.name for t in tenants]
        db = dict.fromkeys(names, 0)
        fs = dict.fromkeys(names, 0)
        if not names:
            return {}
        try:
            conn = odoo.sql_db.db_connect("postgres")
            with closing(conn.cursor()) as cr:
                cr.execute(
                    "SELECT datname, pg_database_size(datname) FROM pg_database "
                    "WHERE datname = ANY(%s)", (names,))
                for name, size in cr.fetchall():
                    db[name] = int(size)
        except Exception:  # noqa: BLE001
            _logger.exception("SaaS: could not read tenant database sizes")
        cfg = odoo.tools.config
        path_to_name = {cfg.filestore(n): n for n in names}
        existing = [p for p in path_to_name if os.path.isdir(p)]
        if existing:
            try:
                out = subprocess.run(["du", "-sb"] + existing,
                                     capture_output=True, text=True, timeout=60)
                for line in out.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 2 and parts[1] in path_to_name:
                        fs[path_to_name[parts[1]]] = int(parts[0])
            except Exception:  # noqa: BLE001
                _logger.exception("SaaS: could not read tenant filestore sizes")
        return {t.id: {"db": db[t.name], "files": fs[t.name]} for t in tenants}

    # ------------------------------------------------------------------
    # Quota enforcement (soft suspension via the reverse proxy)
    # ------------------------------------------------------------------

    def _enforce_quota(self):
        """Flip 'suspended' from real usage vs quota for the active tenants here.

        usage_bytes > quota_bytes -> suspended (quota 0 suspends on any usage).
        Auto-clears when usage is back within quota (e.g. the quota was raised).
        Only the control-plane 'suspended' flag changes -- the tenant database
        is never touched. update_tenant_list.sh then routes suspended tenants to
        the upgrade page within a couple of seconds.
        """
        active = self.filtered(lambda t: t.state == "active")
        if not active:
            return
        usage = active._disk_usage(active)
        for tenant in active:
            u = usage.get(tenant.id, {})
            total = u.get("db", 0) + u.get("files", 0)
            over = total > (tenant.quota_gb or 0) * 1e9
            if tenant.suspended != over:
                tenant.suspended = over
                tenant._log("suspended" if over else "unsuspended")
                _logger.info("SaaS: tenant %s %s (usage vs quota)",
                             tenant.name, "SUSPENDED" if over else "restored")

    @api.model
    def _cron_enforce_quota(self):
        """Catch tenants whose data grew past their quota. Runs on a timer."""
        self._assert_control_plane()
        self.search([("state", "=", "active")])._enforce_quota()
        self.env.cr.commit()

    def write(self, vals):
        res = super().write(vals)
        # Changing a quota re-checks enforcement immediately, so setting a small
        # (or zero) quota suspends the tenant within a couple of seconds, and
        # raising it restores access -- no waiting for the timer. Guarded on the
        # key so the enforcer's own suspended-write does not recurse.
        if "quota_gb" in vals:
            self._enforce_quota()
        return res

    @api.model
    def dashboard_data(self):
        """Everything the dashboard needs, in one call."""
        self._check_developer_group()
        tenants = self.search([])
        usage = self._disk_usage(tenants)

        # Show each password exactly once: collect it now, clear the field,
        # return it in this response. The next poll finds admin_password empty.
        # If two requests race at exactly the same moment, at worst the password
        # is shown in both responses -- acceptable given the control-plane
        # context. The record never holds the password beyond the first call.
        to_clear = tenants.filtered(lambda t: t.admin_password)
        password_map = {t.id: t.admin_password for t in to_clear}
        if to_clear:
            # write() is overridden on saas.audit.log, not here; this is fine.
            to_clear.write({"admin_password": False})

        cfg = self.env["saas.config"]._get()
        return {
            "base_domain": self._base_domain(),
            "pricing": {
                "currency": cfg.price_currency or "EGP",
                "per_gb": cfg.price_per_gb or 0.0,
            },
            "tenants": [
                {
                    "id": t.id,
                    "name": t.name,
                    "company_name": t.company_name or "",
                    "state": t.state,
                    "url": t.url,
                    "admin_login": t.admin_login,
                    "admin_password": password_map.get(t.id, ""),
                    "error_message": t.error_message or "",
                    "disk_db_bytes": usage.get(t.id, {}).get("db", 0),
                    "disk_fs_bytes": usage.get(t.id, {}).get("files", 0),
                    "quota_gb": t.quota_gb or 0.0,
                    "suspended": t.suspended,
                }
                for t in tenants
            ],
        }

    @api.model
    def create_tenant(self, name, company_name=None):
        """Create the record and queue it, in one call from the dashboard."""
        self._check_developer_group()
        tenant = self.create({
            "name": (name or "").strip().lower(),
            "company_name": company_name or False,
        })
        tenant.action_provision()
        return tenant.id
