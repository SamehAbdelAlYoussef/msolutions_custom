# Part of Odoo. See LICENSE file for full copyright and licensing details.
{   'name': 'Accounting',
    'version': '1.1',
    'category': 'msolutions Accounting',
    'sequence': 30,
    'summary': 'Manage financial and analytic accounting',
    'description': '\n'
                   'Accounting Access Rights\n'
                   '========================\n'
                   'It gives the Administrator user access to all accounting features such as journal items '
                   'and the chart of accounts.\n'
                   '\n'
                   'It assigns manager and user access rights to the Administrator for the accounting '
                   'application and only user rights to the Demo user.\n',
    'website': 'https://msolutions.example.com',
    'depends': ['account_reports'],
    'data': [   'data/account_accountant_data.xml',
                'security/accounting_security.xml',
                'views/accountant_menuitem.xml',
                'views/res_config_settings.xml',
                'views/partner_views.xml'],
    'demo': ['demo/account_accountant_demo.xml'],
    'installable': True,
    'application': False,
    'post_init_hook': '_accounting_post_init',
    'uninstall_hook': 'uninstall_hook',
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {   'web.assets_backend': ['accountant/static/src/js/tours/accountant.js'],
                  'web.assets_tests': ['accountant/static/tests/tours/*']},
    'icon': '/accountant/static/description/icon.png'}
