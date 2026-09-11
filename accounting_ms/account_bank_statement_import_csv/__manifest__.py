# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Import CSV Bank Statement',
    'category': 'msolutions Accounting',
    'version': '1.0',
    'description': '\n'
                   'Module to import CSV bank statements.\n'
                   '======================================\n'
                   '\n'
                   'This module allows you to import CSV Files in Odoo: they are parsed and stored in human '
                   'readable format in\n'
                   'Accounting \\ Bank and Cash \\ Bank Statements.\n'
                   '\n'
                   'Important Note\n'
                   '---------------------------------------------\n'
                   "Because of the CSV format limitation, we cannot ensure the same transactions aren't "
                   'imported several times or handle multicurrency.\n'
                   'Whenever possible, you should use a more appropriate file format like OFX.\n',
    'depends': ['account_bank_statement_import', 'base_import'],
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {'web.assets_backend': ['account_bank_statement_import_csv/static/src/**/*']},
    'website': 'https://msolutions.example.com',
    'icon': '/account_bank_statement_import_csv/static/description/icon.png'}
