# -*- coding: utf-8 -*-
{
    'name': 'Product Card (A4)',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Print a product card on A4: image, name, description and code',
    'description': """
Product Card (A4)
=================
Adds Print > "Product Card (A4)" on products (and product variants). One A4
page per product with:

* product image
* product name
* product description
* product code (internal reference)
    """,
    'author': 'Msolutions',
    'depends': ['product'],
    'data': [
        'report/product_card_report.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
