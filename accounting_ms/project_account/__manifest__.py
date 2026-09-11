# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{   'name': 'Project - Account',
    'summary': 'project profitability items computation',
    'description': '\n'
                   'Allows the computation of some section for the project profitability\n'
                   '==================================================================================================\n'
                   "This module allows the computation of the 'Vendor Bills', 'Other Costs' and 'Other "
                   "Revenues' section for the project profitability, in the project update view.\n",
    'category': 'msolutions Accounting',
    'depends': ['account', 'project'],
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'data': [   'views/account_analytic_line_views.xml',
                'views/project_project_views.xml',
                'views/project_task_views.xml',
                'views/project_sharing_project_task_views.xml'],
    'website': 'https://msolutions.example.com',
    'icon': '/project_account/static/description/icon.png'}
