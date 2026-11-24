# -*- coding: utf-8 -*-
{
    'name': 'Sale Scale',
    'version': '19.0.1.1.0',
    'category': 'Sales',
    'summary': 'Scale with discount on sale & purchase order lines',
    'description': """
Sale Scale
==========
- Scale model with a discount value
- Scale + Scale Discount % + Qty After Discount fields on sale & purchase order lines
- Scale discount auto-filled when a scale is selected
- Scale menu under Sales and Purchase
- Auto-validate stock pickings on PO/SO confirmation
- Customer field on PO lines with auto-creation of linked sale orders
- Sales Price on PO lines becomes the unit price of the linked sale order line
- Vendor bill created and posted (bill date = today) on PO confirmation
- Customer invoice created and posted on SO confirmation
    """,
    'author': 'Touch Sales',
    'depends': ['sale_management', 'purchase', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/scale_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
