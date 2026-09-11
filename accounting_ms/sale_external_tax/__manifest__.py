# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
# Technical name `sale_external_tax` is preserved: account_avatax_sale depends
# on it and relies on `sale.order` inheriting `account.external.tax.mixin`
# (which is what provides the `is_avatax` field used by its views).
{   'name': 'msolutions 3rd Party Tax Calculation for Sale',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'Common interface for outsourcing sales tax calculation.',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    'depends': ['account_external_tax', 'sale'],
    'data': ['views/sale_order_views.xml'],
    'installable': True,
    'application': False,
    'auto_install': False,
    'icon': '/sale_external_tax/static/description/icon.png'}
