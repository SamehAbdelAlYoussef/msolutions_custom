import logging
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import closing

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# A template database name is interpolated into DDL (via SQL.identifier, so it
# is quoted -- but we still constrain it so an operator cannot point a plan at,
# say, 'postgres'). Underscores are allowed here (tpl_basic), unlike tenant
# names, because a template is never a DNS label. The 'tpl_' prefix keeps the
# template namespace disjoint from tenant names.
TEMPLATE_NAME_RE = r"^tpl_[a-z][a-z0-9_]{1,58}$"


class SaasPlan(models.Model):
    _name = "saas.plan"
    _description = "SaaS Plan (template-backed tenant tier)"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    template_db = fields.Char(
        string="Template Database",
        copy=False,
        help="PostgreSQL database cloned to provision a tenant on this plan "
             "(must start with 'tpl_', e.g. tpl_basic). Leave empty to build "
             "every tenant on this plan from scratch. Build or refresh it with "
             "/opt/scripts/rebuild_templates.sh.",
    )
    # Apps baked into this plan's template by the rebuild script, and installed
    # directly when a tenant has to fall back to a from-scratch build because
    # the template is missing.
    module_ids = fields.Many2many(
        "ir.module.module",
        "saas_plan_module_rel", "plan_id", "module_id",
        string="Modules",
        domain="[]",
        help="Apps installed into this plan's template database. Edit, then "
             "rebuild the template so the change takes effect.",
    )
    template_last_rebuilt = fields.Datetime(
        string="Template Rebuilt", readonly=True, copy=False,
        help="When rebuild_templates.sh last rebuilt this plan's template.",
    )
    template_exists = fields.Boolean(compute="_compute_template_status")
    template_age_display = fields.Char(
        string="Template Age", compute="_compute_template_status",
        help="Templates go stale after every Odoo upgrade or module change. "
             "Rebuild them when this reads more than a few days old.",
    )

    _template_db_uniq = models.Constraint(
        "UNIQUE (template_db)",
        "Two plans cannot share one template database.",
    )

    @api.constrains("template_db")
    def _check_template_db(self):
        for plan in self:
            if plan.template_db and not re.match(TEMPLATE_NAME_RE, plan.template_db):
                raise ValidationError(_(
                    "'%(tpl)s' is not a valid template name.\n\n"
                    "Use 'tpl_' followed by lowercase letters, digits and "
                    "underscores, e.g. tpl_basic.",
                    tpl=plan.template_db,
                ))

    def _compute_template_status(self):
        """Report which templates exist and how old they are.

        One query against the postgres database for the whole recordset (a
        handful of plans, never per-tenant), so it stays cheap at any tenant
        count. Never opens a template registry.
        """
        names = [p.template_db for p in self if p.template_db]
        existing = set()
        if names:
            conn = odoo.sql_db.db_connect("postgres")
            with closing(conn.cursor()) as cr:
                cr.execute(
                    "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                    (list(names),),
                )
                existing = {r[0] for r in cr.fetchall()}
        now = fields.Datetime.now()
        for plan in self:
            plan.template_exists = bool(plan.template_db and plan.template_db in existing)
            if not plan.template_db:
                plan.template_age_display = _("no template — built from scratch")
            elif not plan.template_exists:
                plan.template_age_display = _("MISSING — run the rebuild script")
            elif not plan.template_last_rebuilt:
                plan.template_age_display = _("built (age unknown)")
            else:
                days = (now - plan.template_last_rebuilt).days
                if days <= 0:
                    plan.template_age_display = _("rebuilt today")
                elif days == 1:
                    plan.template_age_display = _("1 day old")
                else:
                    plan.template_age_display = _("%s days old", days)

    # ------------------------------------------------------------------
    # Template rebuild  (invoked from /opt/scripts/rebuild_templates.sh)
    #
    # This does NOT belong in a web request: installing modules takes minutes
    # and runs an odoo-bin subprocess. Run it from the worker via the script.
    # ------------------------------------------------------------------

    @api.model
    def _rebuild_all_templates(self):
        """Rebuild every plan that has a template. Returns a per-plan report."""
        report = []
        for plan in self.search([("template_db", "!=", False)]):
            try:
                secs = plan._rebuild_template()
                report.append((plan.template_db, "ok", secs))
            except Exception as exc:  # noqa: BLE001 - reported, next plan continues
                _logger.exception("SaaS: rebuild of template %s failed", plan.template_db)
                report.append((plan.template_db, "FAILED: %s" % exc, 0))
        for tpl, status, secs in report:
            _logger.info("SaaS: template %s -> %s (%.0fs)", tpl, status, secs)
        return report

    def _rebuild_template(self):
        """Drop and rebuild this plan's template from scratch.

        drop -> create -> install base + plan modules -> bake the ownership
        split -> mark IS_TEMPLATE / block connections -> stamp the rebuild time.
        Raises on failure, leaving the template dropped rather than half-live.
        """
        self.ensure_one()
        self._check_template_db()
        tpl = self.template_db
        if not tpl:
            raise UserError(_("Plan %s has no template database.", self.name))
        t0 = time.time()

        # 1) Tear down any previous template (and its filestore).
        self._drop_template_db(tpl)

        # 2) Create + install base and the plan's modules, via an odoo-bin
        #    subprocess so the template registry loads and exits in that
        #    process -- never inside this worker.
        modules = list(dict.fromkeys(["base"] + self.module_ids.mapped("name")))
        cfg = odoo.tools.config
        cmd = [
            sys.executable, sys.argv[0], "-c", cfg.rcfile,
            "-d", tpl, "-i", ",".join(modules),
            "--stop-after-init", "--no-http",
        ]
        _logger.info("SaaS: building template %s with modules %s", tpl, modules)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=3600)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode(errors="replace")
            self._drop_template_db(tpl)  # leave nothing half-built behind
            raise UserError(_(
                "Building template %(tpl)s failed:\n%(err)s",
                tpl=tpl, err=stderr[-800:] or str(exc),
            )) from exc

        # 3) Bake the ownership split into the template: objects -> odoo_web,
        #    database stays owned by odoo_provision. A tenant cloned from this
        #    inherits the split; the per-tenant grant then only has to re-add
        #    the database-level CONNECT that CREATE DATABASE does not copy.
        Tenant = self.env["saas.tenant"]
        conn = odoo.sql_db.db_connect(tpl)
        with closing(conn.cursor()) as cr:
            cr.execute(Tenant._GRANT_WEB_OWNERSHIP_SQL)
            cr.execute(odoo.tools.SQL(
                "GRANT CONNECT ON DATABASE %s TO odoo_web",
                odoo.tools.SQL.identifier(tpl)))
            cr._cnx.commit()

        # 4) Mark it a template and refuse client connections, so the wildcard
        #    router can never serve tpl_x.<domain> as a tenant, and so
        #    CREATE DATABASE ... TEMPLATE always sees zero sessions.
        pg = odoo.sql_db.db_connect("postgres")
        with closing(pg.cursor()) as cr:
            cr._cnx.autocommit = True
            cr.execute(odoo.tools.SQL(
                "ALTER DATABASE %s WITH IS_TEMPLATE true ALLOW_CONNECTIONS false",
                odoo.tools.SQL.identifier(tpl)))

        secs = time.time() - t0
        self.template_last_rebuilt = fields.Datetime.now()
        _logger.info("SaaS: template %s rebuilt in %.0fs", tpl, secs)
        return secs

    def _drop_template_db(self, tpl):
        """Drop a template database and its filestore if they exist.

        Clears IS_TEMPLATE and re-allows connections first (Postgres refuses to
        DROP a template), then terminates stragglers and drops. Owner privilege
        (odoo_provision) is enough for all of this; no superuser needed.
        """
        conn = odoo.sql_db.db_connect("postgres")
        with closing(conn.cursor()) as cr:
            cr._cnx.autocommit = True
            cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (tpl,))
            if not cr.fetchone():
                return
            cr.execute(odoo.tools.SQL(
                "ALTER DATABASE %s WITH IS_TEMPLATE false ALLOW_CONNECTIONS true",
                odoo.tools.SQL.identifier(tpl)))
            cr.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (tpl,))
            cr.execute(odoo.tools.SQL("DROP DATABASE %s",
                                      odoo.tools.SQL.identifier(tpl)))
        fs = odoo.tools.config.filestore(tpl)
        if os.path.isdir(fs):
            shutil.rmtree(fs, ignore_errors=True)
        _logger.info("SaaS: dropped template %s", tpl)
