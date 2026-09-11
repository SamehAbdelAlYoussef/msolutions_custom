# -*- coding: utf-8 -*-
# msolutions - branded Community accounting distribution.
{   'name': 'msolutions Accounting',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'msolutions-branded Accounting suite for Odoo Community',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    # This meta-module pulls in the accounting suite only. Integrations that
    # would drag in an unrelated Odoo app (Project, Manufacturing, Point of
    # Sale, Fleet, Inventory) are deliberately NOT listed below. They are kept
    # in the distribution and every one of them is 'auto_install': True with
    # its own app dependency, so it installs itself again the moment that app
    # is installed. Nothing is removed, it is only decoupled:
    #
    #   project_account, project_account_asset, project_account_budget -> Project
    #   project_mrp_account -> Project + Manufacturing
    #   mrp_account, mrp_accountant                                     -> Manufacturing
    #   pos_account_reports                                             -> Point of Sale
    #   account_accountant_fleet, account_asset_fleet,
    #   account_fiscal_categories_fleet                                 -> Fleet
    #   account_avatax_stock                                            -> Inventory
    #
    # stock_accountant is the one exception: it is 'auto_install': False, but
    # it is meaningless without Inventory anyway - its only content is a
    # settings block injected into stock's own res.config.settings form via
    # inherit_id="stock.res_config_settings_view_form". mrp_accountant still
    # declares it as a dependency, so it returns together with Manufacturing.
    'depends': [   'account',
                   'account_3way_match',
                   'account_accountant',
                   'account_accountant_check_printing',
                   'account_asset',
                   'account_avatax',
                   'account_avatax_geolocalize',
                   'account_avatax_sale',
                   'account_bank_statement_import',
                   'account_bank_statement_import_csv',
                   'account_bank_statement_import_ofx',
                   'account_bank_statement_import_qif',
                   'account_base_import',
                   'account_budget',
                   'account_budget_purchase',
                   'account_check_printing',
                   'account_edi',
                   'account_edi_proxy_client',
                   'account_edi_ubl_cii',
                   'account_external_tax',
                   'account_fiscal_categories',
                   'account_followup',
                   'account_inter_company_rules',
                   'account_intrastat',
                   'account_loans',
                   'account_online_synchronization',
                   'account_payment',
                   'account_peppol',
                   'account_peppol_advanced_fields',
                   'account_qr_code_emv',
                   'account_qr_code_sepa',
                   'account_reports',
                   'account_reports_cash_basis',
                   'account_tax_python',
                   'account_transfer',
                   'account_update_tax_tags',
                   'accountant',
                   'msolutions_account_bridge',
                   'purchase_accountant',
                   'sale_external_tax'],
    'data': ['views/msolutions_accounting_menus.xml'],
    'application': True,
    'auto_install': False,
    'installable': True,
    'sequence': 1,
    'icon': '/msolutions_accounting/static/description/icon.png'}
