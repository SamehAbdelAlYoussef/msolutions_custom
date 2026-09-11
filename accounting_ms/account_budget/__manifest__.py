# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Budget Management',
    'category': 'msolutions Accounting',
    'description': '\n'
                   'Use budgets to compare actual with expected revenues and costs\n'
                   '--------------------------------------------------------------\n',
    'depends': ['accountant'],
    'data': [   'security/ir.model.access.csv',
                'security/account_budget_security.xml',
                'wizards/budget_split_wizard_view.xml',
                'views/budget_analytic_views.xml',
                'views/budget_line_view.xml',
                'views/account_analytic_account_views.xml',
                'reports/budget_report_view.xml'],
    'demo': ['data/account_budget_demo.xml'],
    'author': 'msolutions',
    'license': 'OPL-1',
    'post_init_hook': 'post_init_hook',
    'website': 'https://msolutions.example.com',
    'icon': '/account_budget/static/description/icon.png'}
