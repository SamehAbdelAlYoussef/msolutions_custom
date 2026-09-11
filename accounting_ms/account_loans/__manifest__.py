{   'name': 'Loans Management',
    'description': '\n'
                   'Loans management\n'
                   '=================\n'
                   'Keeps track of loans, and creates corresponding journal entries.\n'
                   '    ',
    'category': 'msolutions Accounting',
    'sequence': 32,
    'depends': ['account_asset', 'base_import'],
    'data': [   'security/account_loans_security.xml',
                'security/ir.model.access.csv',
                'wizard/account_loan_close_wizard.xml',
                'wizard/account_loan_compute_wizard.xml',
                'views/account_asset_views.xml',
                'views/account_asset_group_views.xml',
                'views/account_loan_views.xml',
                'views/account_move_views.xml',
                'data/account_return_check_template.xml'],
    'demo': ['demo/account_loans_demo.xml'],
    'author': 'msolutions',
    'license': 'OPL-1',
    'auto_install': True,
    'post_init_hook': '_account_loans_post_init',
    'assets': {'web.assets_backend': ['account_loans/static/src/**/*']},
    'website': 'https://msolutions.example.com',
    'icon': '/account_loans/static/description/icon.png'}
