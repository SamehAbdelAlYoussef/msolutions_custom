# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Budget Management',
    'category': 'msolutions Accounting',
    'description': '\n'
                   'Use budgets to compare actual with expected revenues and costs\n'
                   '--------------------------------------------------------------\n',
    'depends': ['account_budget', 'purchase'],
    'data': [   'views/account_analytic_account_views.xml',
                'views/budget_analytic_views.xml',
                'views/budget_line_view.xml',
                'views/purchase_views.xml',
                'reports/budget_report_view.xml'],
    'demo': ['demo/account_budget_demo.xml'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_budget_purchase/static/description/icon.png'}
