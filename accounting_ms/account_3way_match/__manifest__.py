# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Vendor Bill: Release to Pay',
    'category': 'msolutions Accounting',
    'description': '\n'
                   'Manage 3-way matching on vendor bills\n'
                   '=====================================\n'
                   '\n'
                   'In the manufacturing industry, people often receive the vendor bills before\n'
                   "receiving their purchase, but they don't want to pay the bill until the goods\n"
                   'have been delivered.\n'
                   '\n'
                   'The solution to this situation is to create the vendor bill when you get it\n'
                   '(based on ordered quantities) but only pay the invoice when the received\n'
                   'quantities (on the PO lines) match the recorded vendor bill.\n'
                   '\n'
                   'This module introduces a "release to pay" mechanism that marks for each vendor\n'
                   'bill whether it can be paid or not.\n'
                   '\n'
                   'Each vendor bill receives one of the following three states:\n'
                   '\n'
                   '    - Yes (The bill can be paid)\n'
                   '    - No (The bill cannot be paid, nothing has been delivered yet)\n'
                   '    - Exception (Received and invoiced quantities differ)\n'
                   '    ',
    'depends': ['purchase'],
    'data': ['views/account_invoice_view.xml', 'views/account_journal_dashboard_view.xml'],
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_3way_match/static/description/icon.png'}
