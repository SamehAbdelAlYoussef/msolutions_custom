{
    'name': 'MSolutions SaaS',
    'summary': 'Multi-tenant Odoo SaaS control plane: instant provisioning, '
               'storage quotas, usage-based billing and quota enforcement',
    'description': """
MSolutions SaaS — control plane
===============================

Run an Odoo multi-tenant SaaS (one database + one subdomain per tenant) from a
single backend dashboard. See ``CATALOG.md`` for the full feature catalog.

Highlights
----------
* **Instant provisioning** — clone a template database in ~1 second (with a
  from-scratch fallback); new tenants default to a configurable plan.
* **Tenant management** — auto-discovery of existing databases, complete
  no-trace deletion, a stuck-provision watchdog, and pre-drop safety backups.
* **Real storage metering** — live database + filestore usage per tenant, with
  a usage breakdown chart.
* **Per-tenant storage quotas** — a used/quota gauge with near-full / full
  signals; editable per customer.
* **Usage-based pricing** — monthly invoice = quota (GB) × price per GB.
* **Quota enforcement** — over-quota tenants are suspended at the reverse proxy
  and shown a bilingual "storage full / upgrade" page; raising the quota
  restores access within seconds, and no tenant database is ever touched.

All tenant access uses raw psycopg (never a tenant ORM registry); the split
PostgreSQL roles keep the web tier unable to drop databases.
    """,
    'version': '19.0.15.0.0',
    'category': 'Administration',
    'author': 'Msolutions',
    'license': 'LGPL-3',
    # html_editor is not used by this module's code. It is here because the
    # control-plane database is otherwise tiny, and core web's
    # color_picker.scss references $o-we-sidebar-content-field-spacing,
    # which only html_editor defines. Without it both asset bundles fail to
    # compile and Odoo serves its '## CSS error message ##' placeholder --
    # the whole backend renders as unstyled HTML.
    'depends': ['base', 'web', 'html_editor'],
    'data': [
        'security/saas_security.xml',
        'security/ir.model.access.csv',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'data/saas_plan_data.xml',
        'views/saas_tenant_views.xml',
        'views/saas_plan_views.xml',
        'views/saas_audit_log_views.xml',
        'views/saas_config_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'msolutions_saas/static/src/saas_dashboard/**/*',
            'msolutions_saas/static/src/saas_shell/**/*',
            'msolutions_saas/static/src/saas_logs/**/*',
        ],
    },
    'installable': True,
    # Deliberately NOT an application. As the only app in the control-plane
    # database it became the web client's default landing action, so /odoo
    # jumped straight into the tenant dashboard instead of the home screen.
    # It is an administration tool; it keeps its own top-level menu.
    'application': False,
    'auto_install': False,
}
