{
    'name': 'SaaS dbfilter patch',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'summary': 'Enumerate tenant databases by CONNECT privilege, not ownership',
    'description': """
Loaded via server_wide_modules. Odoo core list_dbs() enumerates only databases
OWNED by the connecting role (datdba=current_user). In this SaaS the web/gevent
tier connects as odoo_web, which deliberately does NOT own the tenant databases
(so it cannot DROP them) -- odoo_provision owns them. Without this patch dbfilter
finds nothing and every subdomain falls through to the database selector.
This patch swaps the owner filter for a CONNECT-privilege filter.
""",
    'author': 'Msolutions',
    'license': 'LGPL-3',
    'depends': ['base'],
    'installable': True,
    'auto_install': False,
    'application': False,
}
