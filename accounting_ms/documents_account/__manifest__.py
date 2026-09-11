# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Documents - Accounting',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Invoices from Documents [msolutions: requires an unemulated Enterprise module]',
    'description': '\n'
                   'Bridge module between the accounting and documents apps. It enables\n'
                   'the creation invoices from the Documents module, and adds a\n'
                   "button on Accounting's reports allowing to save the report into the\n"
                   'Documents app in the desired format(s).\n',
    'website': 'https://msolutions.example.com',
    'depends': ['msolutions_account_bridge', 'account_reports'],
    'data': [   'security/ir.model.access.csv',
                'security/security.xml',
                'data/documents_account_tour.xml',
                'data/ir_actions_server_data.xml',
                'views/account_move_views.xml',
                'views/documents_account_folder_setting_views.xml',
                'views/documents_document_views.xml',
                'views/ir_actions_server_views.xml',
                'views/res_config_settings_views.xml',
                'wizard/account_reports_export_wizard_views.xml'],
    'installable': False,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {   'web.assets_backend': [   'documents_account/static/**/*',
                                            ('remove', 'documents_account/static/src/views/activity/**')],
                  'web.assets_backend_lazy': ['documents_account/static/src/views/activity/**']},
    'post_init_hook': '_documents_account_post_init',
    'icon': '/documents_account/static/description/icon.png'}
