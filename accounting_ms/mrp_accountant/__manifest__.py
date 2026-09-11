# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Mrp Accounting',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Bridge between Mrp and Accounting',
    'description': '\nAutomatic accounting for MRP\n    ',
    'depends': ['mrp_account', 'stock_accountant'],
    'data': ['views/res_config_settings_views.xml'],
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/mrp_accountant/static/description/icon.png'}
