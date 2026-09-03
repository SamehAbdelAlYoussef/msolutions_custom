import logging
import os
import re
import secrets
import shutil
import string
from contextlib import closing

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
                    "Use 4 to 31 characters: lowercase letters and digits only, "
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

    def action_provision(self):
        """Queue the tenant for creation. The cron does the work."""
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
        self.write({"state": "provisioning", "error_message": False})
        self._trigger_cron()
        return True

    def action_drop(self):
        """Queue the tenant for deletion. The cron does the work.

        The confirmation lives in the client action; by the time this runs the
        user has already typed the tenant name back.
        """
        self._assert_control_plane()
        for tenant in self:
            if tenant.state not in ("active", "error"):
                raise UserError(_(
                    "Tenant %(name)s is %(state)s and cannot be dropped.",
                    name=tenant.name, state=tenant.state,
                ))
        self.write({"state": "terminating", "error_message": False})
        self._trigger_cron()
        return True

    def action_open_url(self):
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
        tenant = self.search([("state", "=", "provisioning")], limit=1)
        if tenant:
            tenant._run_provision()
            return

        tenant = self.search([("state", "=", "terminating")], limit=1)
        if tenant:
            tenant._run_drop()

    def _run_provision(self):
        self.ensure_one()
        _logger.info("SaaS: provisioning tenant %s", self.name)
        try:
            self._provision_database()
            self._provision_odoo()
            self.write({"state": "active", "error_message": False})
            _logger.info("SaaS: tenant %s is active", self.name)
        except Exception as exc:  # noqa: BLE001 - recorded on the record
            _logger.exception("SaaS: provisioning tenant %s failed", self.name)
            self.write({"state": "error", "error_message": str(exc)})
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
            _logger.info("SaaS: tenant %s terminated", self.name)
        except Exception as exc:  # noqa: BLE001 - recorded on the record
            _logger.exception("SaaS: dropping tenant %s failed", self.name)
            self.write({"state": "error", "error_message": str(exc)})
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

    def _drop_database(self):
        """Drop the database and its filestore.

        The filestore removal is the part delete_tenant.sh missed -- there are
        orphaned directories under the data_dir from tenants dropped earlier.
        """
        self.ensure_one()
        if not self._database_exists():
            _logger.info("SaaS: database %s already gone", self.name)
            return
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
