/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";

/**
 * Intercept kanban drag-and-drop: when a visit line is dragged from
 * "First Visit" to "Repeat Visit", show the convert-to-repeat wizard
 * instead of moving the record directly.
 */
patch(KanbanRenderer.prototype, {
    async sortRecordDrop(dataRecordId, dataGroupId, { element, parent, previous }) {
        const groups = this.props.list.groups;
        const targetGroup = groups.find((g) => g.id === parent?.dataset?.id);

        if (
            targetGroup &&
            targetGroup.value === "repeat_visit" &&
            dataGroupId
        ) {
            const sourceGroup = groups.find((g) => g.id === dataGroupId);
            if (sourceGroup && sourceGroup.value === "first_visit") {
                const record = this.props.list.records.find(
                    (r) => r.id === dataRecordId
                );
                if (record) {
                    const { action: actionService } = this.env.services;
                    const self = this;
                    // Open the wizard via doActionButton (same as clicking the button)
                    // The onClose callback reloads the kanban after wizard closes
                    await actionService.doActionButton({
                        name: "action_convert_to_repeat",
                        type: "object",
                        resModel: "sales.plan.line",
                        resId: record.resId,
                        context: record.context,
                        onClose: async () => {
                            await self.props.list.model.load();
                        },
                    });
                    return; // Skip normal drag-drop
                }
            }
        }

        return super.sortRecordDrop(dataRecordId, dataGroupId, {
            element,
            parent,
            previous,
        });
    },
});
