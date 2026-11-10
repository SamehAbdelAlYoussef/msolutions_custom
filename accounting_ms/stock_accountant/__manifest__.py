# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
# Technical name `stock_accountant` is preserved: mrp_accountant inherits the
# xml-id `stock_accountant.res_config_settings_view_form`.
{   'name': 'msolutions Stock Accounting',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'Inventory valuation settings surface for the msolutions accounting suite.',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    'depends': ['account', 'stock_account', 'account_accountant', 'account_reports'],
    'data': [
        'report/stock_valuation_report.xml',
        'views/res_config_settings_views.xml',
        'views/product_category_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
    'icon': '/stock_accountant/static/description/icon.png'}
