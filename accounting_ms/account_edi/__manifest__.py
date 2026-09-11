# -*- coding: utf-8 -*-
{   'name': 'Import/Export Invoices From XML/PDF',
    'description': '\n'
                   'Electronic Data Interchange\n'
                   '=======================================\n'
                   'EDI is the electronic interchange of business information using a standardized format.\n'
                   '\n'
                   'This is the base module for import and export of invoices in various EDI formats, and '
                   'the\n'
                   'the transmission of said documents to various parties involved in the exchange (other '
                   'company,\n'
                   'governements, etc.)\n'
                   '    ',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'depends': ['account'],
    'data': [   'security/ir.model.access.csv',
                'views/account_edi_document_views.xml',
                'views/account_move_views.xml',
                'views/account_journal_views.xml',
                'data/cron.xml'],
    'installable': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_edi/static/description/icon.png'}
