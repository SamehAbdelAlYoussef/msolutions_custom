# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Project Budget',
    'version': '1.0',
    'summary': 'Project account budget',
    'category': 'msolutions Accounting',
    'depends': ['msolutions_account_bridge', 'account_budget'],
    'data': [   'views/project_project_views.xml',
                'views/budget_analytic_views.xml',
                'views/project_update_templates.xml'],
    'demo': ['data/budget_analytic_demo.xml'],
    'assets': {'web.assets_backend': ['project_account_budget/static/src/components/**/*']},
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/project_account_budget/static/description/icon.png'}
