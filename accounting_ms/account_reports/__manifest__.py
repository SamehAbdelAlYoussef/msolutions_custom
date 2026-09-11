# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
# Technical name `account_reports` is intentionally preserved: it is a Python
# import path (`odoo.addons.account_reports...`) and the xml-id namespace that
# the ported modules reference. Only the metadata is re-branded.
{   'name': 'msolutions Reports Engine',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'Financial report runtime, return models and xml-id compatibility surface for the msolutions '
               'accounting suite on Odoo Community.',
    'description': '\n'
                   'msolutions Reports Engine\n'
                   '=========================\n'
                   'Provides the Community replacement for the Enterprise ``account_reports``\n'
                   'runtime:\n'
                   '\n'
                   '* extends the Community ``account.report`` data models with the rendering engine\n'
                   '  (options, line ids, column dictionaries, prefix grouping);\n'
                   '* declares ``account.report.custom.handler`` and the ``account.return`` family;\n'
                   '* declares ``account.tax.unit``;\n'
                   '* re-declares the xml-ids consumed by every ported module so their views, data\n'
                   '  and menus load without ``External ID not found``.\n',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    'depends': ['base', 'web', 'account'],
    'data': [   'security/ir.model.access.csv',
                'views/account_report_views.xml',
                'data/account_report_data.xml',
                'data/balance_sheet.xml',
                'data/profit_and_loss.xml',
                'data/cash_flow_report.xml',
                'data/executive_summary.xml',
                'data/aged_partner_balance.xml',
                'data/partner_ledger.xml',
                'data/deferred_reports.xml',
                'data/journal_report.xml',
                'data/multicurrency_revaluation_report.xml',
                'data/general_ledger.xml',
                'data/account_report_actions.xml',
                'data/menuitems.xml',
                'data/account_return_data.xml'],
    'assets': {'web.assets_backend': ['account_reports/static/src/**/*']},
    'installable': True,
    'application': False,
    'auto_install': False,
    'icon': '/account_reports/static/description/icon.png'}
