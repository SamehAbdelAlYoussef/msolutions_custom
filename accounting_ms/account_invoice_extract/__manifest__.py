# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Account Invoice Extract',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Extract data from invoice scans to fill them automatically [msolutions: requires an '
               'unemulated Enterprise module]',
    'depends': ['msolutions_account_bridge', 'account_extract'],
    'data': ['data/crons.xml', 'views/account_move_views.xml', 'views/res_config_settings_views.xml'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {'web.assets_backend': ['account_invoice_extract/static/src/js/*.js']},
    'website': 'https://msolutions.example.com',
    'icon': '/account_invoice_extract/static/description/icon.png',
    'installable': False}
