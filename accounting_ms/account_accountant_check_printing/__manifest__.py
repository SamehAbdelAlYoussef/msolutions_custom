# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Account accountant check printing',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Allows using Reconciliation with the account check printing.',
    'depends': ['account_accountant', 'account_check_printing'],
    'data': ['views/bank_rec_widget_views.xml'],
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_accountant_check_printing/static/description/icon.png'}
