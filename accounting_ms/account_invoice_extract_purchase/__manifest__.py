# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Account Invoice Extract Purchase',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Automatically finds the purchase order linked to a vendor bill when using invoice extraction '
               '[msolutions: requires an unemulated Enterprise module]',
    'depends': ['msolutions_account_bridge', 'account_invoice_extract', 'purchase'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_invoice_extract_purchase/static/description/icon.png',
    'installable': False}
