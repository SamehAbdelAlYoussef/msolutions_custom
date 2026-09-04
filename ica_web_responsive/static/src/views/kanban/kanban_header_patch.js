/* @odoo-module */

import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { GroupConfigMenu } from "@web/views/view_components/group_config_menu";
import { PromoteStudioAutomationDialog } from "@ica_web_responsive/webclient/promote_studio_dialog/promote_studio_dialog";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";

// In Odoo 19, automation actions live in GroupConfigMenu (not KanbanHeader).
// Patch GroupConfigMenu so it has an openAutomations method with access to
// the dialog service; reference it by name from the group_config_items registry.
patch(GroupConfigMenu.prototype, {
    async openAutomations() {
        if (typeof this._openAutomations === "function") {
            // base_automation is installed — delegate to it
            return this._openAutomations();
        }
        this.dialog.add(PromoteStudioAutomationDialog, {
            title: _t("Odoo Studio - Customize workflows in minutes"),
        });
    },
});

// Registry name changed: kanban_header_config_items → group_config_items
// isVisible no longer receives permissions.canEditAutomations; check user directly.
registry.category("group_config_items").add(
    "open_automations",
    {
        label: _t("Automations"),
        method: "openAutomations",
        isVisible: () => user.isAdmin,
        class: "o_column_automations",
    },
    { sequence: 25, force: true }
);
