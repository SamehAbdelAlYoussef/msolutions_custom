# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{   'name': 'Avatax for SO',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'depends': ['sale_external_tax', 'account_avatax', 'sale'],
    'data': ['views/sale_order_views.xml', 'views/sale_portal_templates.xml', 'reports/sale_order.xml'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_avatax_sale/static/description/icon.png'}
