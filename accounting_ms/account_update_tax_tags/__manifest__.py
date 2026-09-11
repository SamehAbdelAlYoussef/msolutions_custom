{   'name': 'Account - Allow updating tax grids',
    'category': 'msolutions Accounting',
    'summary': 'Allow updating tax grids on existing entries',
    'version': '1.0',
    'description': '\n'
                   '    This module allows updating tax grids on existing accounting entries.\n'
                   "    In debug mode a button to update your entries' tax grids will be available\n"
                   '    in Accounting settings.\n'
                   '    This is typically useful after some legal changes were done on the tax report,\n'
                   '    requiring a new tax configuration.\n'
                   '    ',
    'depends': ['account'],
    'data': [   'security/ir.model.access.csv',
                'views/res_config_settings_views.xml',
                'wizard/account_update_tax_tags_wizard.xml'],
    'author': 'msolutions',
    'license': 'OPL-1',
    'website': 'https://msolutions.example.com',
    'icon': '/account_update_tax_tags/static/description/icon.png'}
