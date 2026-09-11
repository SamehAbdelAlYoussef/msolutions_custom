# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
{   'name': 'msolutions Accounting Bridge',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'Cross-cutting gap filler: missing fields, reconciliation wiring and Enterprise compatibility '
               'shims for the msolutions accounting suite.',
    'description': '\n'
                   'msolutions Accounting Bridge\n'
                   '============================\n'
                   'Absorbs the Enterprise capabilities that the ported accounting modules expect\n'
                   'but that Community does not provide:\n'
                   '\n'
                   '* ``account.move.closing_return_id`` and ``account.move.line.exclude_bank_lines``;\n'
                   '* the ``res.company`` return / revaluation / tax-unit relation fields;\n'
                   '* a guarded registration of the bank-reconciliation service and kanban view used\n'
                   '  by ``account_accountant`` (no-op when the ported components are present);\n'
                   '* dependency on the msolutions compatibility shims\n'
                   '  (``account_reports``, ``account_fiscal_categories``,\n'
                   '  ``account_bank_statement_import``) so their xml-ids and models are available.\n',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    'depends': [   'base',
                   'web',
                   'account',
                   'account_reports',
                   'account_fiscal_categories',
                   'account_bank_statement_import'],
    'data': [],
    'assets': {'web.assets_backend': ['msolutions_account_bridge/static/src/**/*']},
    'installable': True,
    'application': False,
    'auto_install': False,
    'icon': '/msolutions_account_bridge/static/description/icon.png'}
