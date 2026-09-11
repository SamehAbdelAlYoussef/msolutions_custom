/** @odoo-module **/
/*
 * msolutions - Community-compatible accounting distribution.
 *
 * Community has no bank reconciliation client at all. In this port the widget
 * ships inside `account_accountant` (Odoo >= 17 merged
 * `account_reconciliation_widget` into it) and is already declared in
 * `web.assets_backend`.
 *
 * This file is therefore a *guarded* injection: it registers the reconciliation
 * service and kanban view only when they are missing, so it is a strict no-op in
 * a normal msolutions install and cannot duplicate registrations.
 */
import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";

if (!registry.category("services").contains("bankReconciliation")) {
    registry.category("services").add("bankReconciliation", {
        dependencies: [],
        start() {
            return {
                reconcile() {
                    return Promise.resolve();
                },
            };
        },
    });
}

if (!registry.category("views").contains("bank_rec_widget_kanban")) {
    // Placeholder view type: avoids "View type not found" when a stored action
    // still references it. The real implementation is provided by
    // account_accountant when installed.
    registry.category("views").add("bank_rec_widget_kanban", {
        ...kanbanView,
        type: "bank_rec_widget_kanban",
    });
}
