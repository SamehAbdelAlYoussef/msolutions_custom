# -*- coding: utf-8 -*-
{   'name': 'Proxy features for account_edi',
    'description': '\n'
                   'This module adds generic features to register an Odoo DB on the proxy responsible for '
                   'receiving data (via requests from web-services).\n'
                   '- An edi_proxy_user has a unique identification on a specific proxy type (e.g. '
                   'l10n_it_edi, peppol) which\n'
                   'allows to identify him when receiving a document addressed to him. It is linked to a '
                   'specific company on a specific\n'
                   'Odoo database.\n'
                   "- Encryption features allows to decrypt all the user's data when receiving it from the "
                   'proxy.\n'
                   '- Authentication offers an additionnal level of security to avoid impersonification, in '
                   "case someone gains to the user's database.\n"
                   '    ',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'depends': ['account', 'certificate'],
    'data': [   'security/ir.model.access.csv',
                'security/account_edi_proxy_client_security.xml',
                'views/account_edi_proxy_user_views.xml'],
    'installable': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'post_init_hook': '_create_demo_config_param',
    'website': 'https://msolutions.example.com',
    'icon': '/account_edi_proxy_client/static/description/icon.png'}
