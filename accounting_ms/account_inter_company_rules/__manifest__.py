# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{   'name': 'Inter Company Module for Sale/Purchase Orders and Invoices',
    'version': '1.1',
    'summary': 'Intercompany SO/PO/INV rules',
    'category': 'msolutions Accounting',
    'description': ' Module for synchronization of Documents between several companies. For example, this '
                   'allow you to have a Sales Order created automatically when a Purchase Order is validated '
                   'with another company of the system as vendor, and inversely.\n'
                   '\n'
                   '    Supported documents are invoices/credit notes.\n',
    'depends': ['account'],
    'data': ['views/res_company_views.xml', 'views/res_config_settings_views.xml'],
    'installable': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_inter_company_rules/static/description/icon.png'}
