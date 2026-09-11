# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Account Winbooks Import',
    'summary': 'Import Data From Winbooks [msolutions: requires an unemulated Enterprise module]',
    'description': '\nImport Data From Winbooks\n    ',
    'category': 'msolutions Accounting',
    'depends': ['account_accountant', 'base_vat', 'account_base_import'],
    'external_dependencies': {'python': ['dbfread'], 'apt': {'dbfread': 'python3-dbfread'}},
    'data': [   'security/ir.model.access.csv',
                'wizard/account_import_summary_views.xml',
                'wizard/import_wizard_views.xml'],
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {'web.assets_backend': ['account_winbooks_import/static/src/xml/**/*']},
    'website': 'https://msolutions.example.com',
    'icon': '/account_winbooks_import/static/description/icon.png',
    'installable': False}
