{   'name': 'Documents - Import from Peppol',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'summary': 'Documents from Peppol [msolutions: requires an unemulated Enterprise module]',
    'description': '\n'
                   'Bridge module between the Documents and Peppol apps.\n'
                   'It allows importing of received Peppol documents\n'
                   'within the Documents app.\n'
                   '    ',
    'author': 'msolutions',
    'license': 'OPL-1',
    'depends': ['msolutions_account_bridge', 'account_peppol'],
    'data': ['views/res_config_settings_views.xml'],
    'auto_install': True,
    'website': 'https://msolutions.example.com',
    'icon': '/documents_account_peppol/static/description/icon.png',
    'installable': False}
