# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Project Budget',
    'version': '1.0',
    'summary': 'Project account budget',
    'category': 'msolutions Accounting',
    # 'project' is a real dependency: models/project_project.py inherits
    # 'project.project' and models/project_update.py inherits 'project.update',
    # and the views extend project.project / project.update templates. Without
    # it, auto_install would trigger as soon as account_budget is present and
    # the registry would fail to load. Declared explicitly so this module only
    # installs when Project itself is installed.
    'depends': ['msolutions_account_bridge', 'account_budget', 'project'],
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
