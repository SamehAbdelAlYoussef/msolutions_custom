# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Purchase Accounting',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Bridge between Purchase and Accounting',
    'description': '\n'
                   'Add accrued menus and specific filters on purchase order lines for an easier closing '
                   'process.\n'
                   '    ',
    'depends': ['purchase', 'account_accountant'],
    'data': ['views/purchase_order_line_views.xml'],
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/purchase_accountant/static/description/icon.png'}
