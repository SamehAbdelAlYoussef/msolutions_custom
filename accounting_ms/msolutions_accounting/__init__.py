# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.

# The seven Accounting tabs this module moves under its own root (see
# views/msolutions_accounting_menus.xml). All of them belong to `account`.
_ACCOUNTING_TABS = (
    "menu_board_journal_1",       # Dashboard
    "menu_finance_receivables",   # Customers
    "menu_finance_payables",      # Vendors
    "menu_finance_entries",       # Accounting
    "account_audit_menu",         # Review
    "menu_finance_reports",       # Reporting
    "menu_finance_configuration", # Configuration
)


def uninstall_hook(env):
    """Give the Accounting tabs back to the original Accounting root.

    On install this module re-parents the seven tabs under its own root and
    deactivates `accountant.menu_accounting`. Uninstalling deletes our root, and
    without this the tabs lose their parent and turn into seven icon-less apps
    on the home screen while the original root stays hidden. The accounting
    modules themselves stay installed, so put the menus back where they were.
    Runs before this module's records are removed.
    """
    root = env.ref("accountant.menu_accounting", raise_if_not_found=False)
    if not root:
        return
    root.active = True
    for name in _ACCOUNTING_TABS:
        tab = env.ref("account." + name, raise_if_not_found=False)
        if tab:
            tab.parent_id = root
