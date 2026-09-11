# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{   'name': 'Payment Follow-up Management',
    'version': '1.1',
    'category': 'msolutions Accounting',
    'description': '\n'
                   'Module to automate letters for unpaid invoices, with multi-level recalls.\n'
                   '=========================================================================\n'
                   '\n'
                   'You can define your multiple levels of recall through the menu:\n'
                   '---------------------------------------------------------------\n'
                   '    Configuration / Follow-up / Follow-up Levels\n'
                   '\n'
                   'Once it is defined, you can automatically print recalls every day through simply '
                   'clicking on the menu:\n'
                   '------------------------------------------------------------------------------------------------------\n'
                   '    Payment Follow-Up / Send Email and letters\n'
                   '\n'
                   'It will generate a PDF / send emails / set activities according to the different levels\n'
                   'of recall defined. You can define different policies for different companies.\n'
                   '\n',
    'website': 'https://msolutions.example.com',
    'depends': ['mail', 'sms', 'account_reports'],
    'data': [   'security/account_followup_security.xml',
                'security/ir.model.access.csv',
                'security/sms_security.xml',
                'data/mail_template.xml',
                'data/account_followup_data.xml',
                'data/cron.xml',
                'wizard/followup_manual_reminder_views.xml',
                'wizard/followup_missing_information.xml',
                'views/account_followup_views.xml',
                'views/account_followup_line_views.xml',
                'views/account_move_views.xml',
                'views/partner_view.xml',
                'views/report_followup.xml'],
    'demo': ['demo/account_followup_demo.xml'],
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'assets': {   'account_followup.assets_followup_report': [   ('include', 'web._assets_helpers'),
                                                                 'web/static/src/scss/pre_variables.scss',
                                                                 'web/static/lib/bootstrap/scss/_variables.scss',
                                                                 'web/static/lib/bootstrap/scss/_variables-dark.scss',
                                                                 'web/static/lib/bootstrap/scss/_maps.scss',
                                                                 ('include', 'web._assets_bootstrap_backend'),
                                                                 'web/static/fonts/fonts.scss'],
                  'web.assets_backend': [   'account_followup/static/src/components/**/*.js',
                                            'account_followup/static/src/components/**/*.scss',
                                            'account_followup/static/src/components/**/*.xml']},
    'icon': '/account_followup/static/description/icon.png'}
