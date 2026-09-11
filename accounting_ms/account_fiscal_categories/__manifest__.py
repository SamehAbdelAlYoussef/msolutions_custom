# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
{   'name': 'msolutions Fiscal Categories',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'Fiscal category and deductibility-rate models for the msolutions accounting suite.',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    'depends': ['account', 'account_reports'],
    'data': [   'security/ir.model.access.csv',
                'views/account_fiscal_category_views.xml',
                'data/account_fiscal_report_data.xml'],
    'installable': True,
    'application': False,
    'auto_install': False,
    'icon': '/account_fiscal_categories/static/description/icon.png'}
