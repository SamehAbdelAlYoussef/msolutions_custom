{   'name': 'Import/Export electronic invoices with UBL/CII',
    'version': '1.0',
    'category': 'msolutions Accounting',
    'description': '\n'
                   'Electronic invoicing module\n'
                   '===========================\n'
                   '\n'
                   'Allows to export and import formats: E-FFF, UBL Bis 3, EHF3, NLCIUS, Factur-X (CII), '
                   'XRechnung (UBL).\n'
                   'When generating the PDF on the invoice, the PDF will be embedded inside the xml for all '
                   'UBL formats. This allows the\n'
                   'receiver to retrieve the PDF with only the xml file. Note that **EHF3 is fully '
                   'implemented by UBL Bis 3** (`reference\n'
                   '<https://anskaffelser.dev/postaward/g3/spec/current/billing-3.0/norway/#_implementation>`_).\n'
                   '\n'
                   'The formats can be chosen from the journal (Journal > Advanced Settings) linked to the '
                   'invoice.\n'
                   '\n'
                   'Note that E-FFF, NLCIUS and XRechnung (UBL) are only available for Belgian, Dutch and '
                   'German companies,\n'
                   'respectively. UBL Bis 3 is only available for companies which country is present in the '
                   '`EAS list\n'
                   '<https://docs.peppol.eu/poacc/billing/3.0/codelist/eas/>`_.\n'
                   '\n'
                   'Note also that in order for Chorus Pro to automatically detect the "PDF/A-3 (Factur-X)" '
                   'format, you need to activate\n'
                   'the "Factur-X PDF/A-3" option on the journal. This option will also validate the xml '
                   'against the Factur-X and Chorus\n'
                   'Pro rules and show the errors.\n'
                   '    ',
    'depends': ['account'],
    'data': ['data/cii_22_templates.xml', 'views/account_tax_views.xml', 'views/res_partner_views.xml'],
    'assets': {'web.assets_backend': ['account_edi_ubl_cii/static/src/scss/**/*']},
    'installable': True,
    'auto_install': True,
    'author': 'msolutions',
    'license': 'OPL-1',
    'uninstall_hook': 'uninstall_hook',
    'website': 'https://msolutions.example.com',
    'icon': '/account_edi_ubl_cii/static/description/icon.png'}
