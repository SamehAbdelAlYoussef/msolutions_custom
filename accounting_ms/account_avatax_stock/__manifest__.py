# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{   'name': 'Avatax for Inventory',
    'version': '1.0',
    'description': '\n'
                   'Inventory management for Avatax\n'
                   '=======================================\n'
                   'This module allows for line-level addresses when getting taxes from avatax.\n'
                   '\n'
                   'A current limitation is a single order line with more than one stock move (i.e. 10 units '
                   'of \n'
                   'product A, 2 shipped from warehouse #1 and 8 from warehouse #2). In this case the sale '
                   'orders should be\n'
                   'split per delivery.\n'
                   '    ',
    'category': 'msolutions Accounting',
    'depends': ['account_avatax_sale', 'stock'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_avatax_stock/static/description/icon.png'}
