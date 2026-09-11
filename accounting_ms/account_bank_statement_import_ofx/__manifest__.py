# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Import OFX Bank Statement',
    'category': 'msolutions Accounting',
    'version': '1.0',
    'depends': ['account_bank_statement_import'],
    'description': '\n'
                   'Module to import OFX bank statements.\n'
                   '======================================\n'
                   '\n'
                   'This module allows you to import the machine readable OFX Files in Odoo: they are parsed '
                   'and stored in human readable format in\n'
                   'Accounting \\ Bank and Cash \\ Bank Statements.\n'
                   '\n'
                   'Bank Statements may be generated containing a subset of the OFX information (only those '
                   'transaction lines that are required for the\n'
                   'creation of the Financial Accounting records).\n'
                   '    ',
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_bank_statement_import_ofx/static/description/icon.png'}
