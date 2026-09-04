{
    'name': 'MSolutions SaaS',
    'summary': 'Create and remove Odoo tenants (database + subdomain) from the backend',
    'description': """
MSolutions SaaS
===============

Replaces ``/opt/scripts/create_tenant.sh`` with a backend screen.

A tenant is one Odoo database served at ``<name>.<base domain>``. Routing is
already handled by ``dbfilter = ^%d$`` in ``odoo.conf`` plus the wildcard
certificate, so provisioning a tenant is: create the database, install ``base``,
set the admin credentials.

The work runs in ``ir.cron``, not in the web request, because installing
``base`` takes about a minute.
    """,
    'version': '19.0.1.0.0',
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
        'views/saas_tenant_views.xml',
        'views/saas_audit_log_views.xml',
        'views/saas_config_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'msolutions_saas/static/src/saas_dashboard/**/*',
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
