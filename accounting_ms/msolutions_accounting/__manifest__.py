# -*- coding: utf-8 -*-
# msolutions - branded Community accounting distribution.
{   'name': 'msolutions Accounting',
    'version': '19.0.1.0.0',
    'category': 'msolutions Accounting',
    'summary': 'msolutions-branded Accounting suite for Odoo Community',
    'author': 'msolutions',
    'website': 'https://msolutions.example.com',
    'license': 'OPL-1',
    # -----------------------------------------------------------------------
    # Dependency strategy — mirrors Enterprise account_accountant pattern:
    #
    # ALWAYS installed with this meta-module (core accounting suite):
    #   All modules in the 'depends' list below.
    #
    # AUTO-INSTALL — NOT listed here, self-install when their trigger is met:
    #   account_avatax            → auto_install=['payment']   (Avalara US Tax)
    #   account_avatax_geolocalize→ auto_install=True          (avatax + geolocalize)
    #   account_avatax_sale       → auto_install=True          (avatax + sale)
    #   account_peppol            → auto_install=['account_edi_ubl_cii'] (PEPPOL e-invoicing)
    #   account_peppol_advanced_fields → deprecated in Enterprise, auto-removed
    #
    # APP-INTEGRATION — auto_install=True when their app is installed:
    #   mrp_account, mrp_accountant          → Manufacturing
    #   project_account, project_account_*   → Project
    #   pos_account_reports                  → Point of Sale
    #   account_accountant_fleet, *_fleet    → Fleet
    #   account_avatax_stock                 → Inventory (stock)
    #   purchase_accountant                  → auto when purchase present
    #   sale_external_tax                    → auto when sale + account_external_tax
    #   stock_accountant                     → auto when stock_account present
    # -----------------------------------------------------------------------
    'depends': [   'account',
                   'account_3way_match',
                   'account_accountant',
                   'account_accountant_check_printing',
                   'account_asset',
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
