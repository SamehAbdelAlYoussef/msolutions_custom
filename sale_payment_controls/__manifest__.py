# -*- coding: utf-8 -*-
{
    'name': 'Sale Payment Controls',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Minimum first advance payment on sales orders and a mandatory '
               'receipt / batch number on payment confirmation',
    'description': """
Sale Payment Controls
=====================
Works on the advance payments registered from a sales order
(``sales_orders_payment_follow``).

* The FIRST payment of a sales order must be at least 50% of the order total
  (configurable with the system parameter
  ``sale_payment_controls.min_down_payment_percent``). Users in the group
  "Allow first payment below minimum" may go below it.
* Payments get a "Receipt / Batch No." field. A payment of a sales order cannot
  be confirmed (posted) until it is filled in.
    """,
    'author': 'Msolutions',
    'depends': ['sales_orders_payment_follow'],
    'data': [
        'security/security.xml',
        'views/account_payment_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
