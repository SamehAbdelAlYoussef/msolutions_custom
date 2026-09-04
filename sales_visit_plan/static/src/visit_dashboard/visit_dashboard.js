import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useState } from "@odoo/owl";

const STAGE_LABELS = {
    first_visit: _t("First Visit"),
    repeat_visit: _t("Repeat Visit"),
    completed: _t("Completed"),
};

const TYPE_LABELS = {
    doctor: _t("Doctor"),
    pharmacy: _t("Pharmacy"),
    meeting: _t("Meeting"),
};

export class VisitDashboard extends Component {
    static template = "sales_visit_plan.VisitDashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("sales.plan", "_dashboard_data", []);
        this.state.loading = false;
    }

    // ----------------------------------------------------------------
    // Computed display helpers
    // ----------------------------------------------------------------

    get todayFormatted() {
        if (!this.state.data) return "";
        // today_iso is "YYYY-MM-DD". Append T00:00 to force local-time parse.
        const d = new Date(this.state.data.today_iso + "T00:00");
        return new Intl.DateTimeFormat(navigator.language, {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
        }).format(d);
    }

    stationClass(line) {
        return {
            "o_svp_station_completed": line.visit_stage === "completed",
            "o_svp_station_repeat": line.visit_stage === "repeat_visit",
        };
    }

    chipClass(line) {
        return {
            "o_svp_chip_doctor": line.visit_type === "doctor",
            "o_svp_chip_pharmacy": line.visit_type === "pharmacy",
            "o_svp_chip_meeting": line.visit_type === "meeting",
        };
    }

    typeLabel(line) {
        return TYPE_LABELS[line.visit_type] || line.visit_type;
    }

    stageLabel(line) {
        return STAGE_LABELS[line.visit_stage] || line.visit_stage;
    }

    // ----------------------------------------------------------------
    // Navigation
    // ----------------------------------------------------------------

    openVisit(line) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sales.plan.line",
            res_id: line.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openPlan(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sales.plan",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    onNewPlan() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sales.plan",
            views: [[false, "form"]],
            target: "current",
        });
    }

    goToPlans() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Plans"),
            res_model: "sales.plan",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            target: "current",
        });
    }

    goToVisitLines() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Visit Lines"),
            res_model: "sales.plan.line",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            target: "current",
        });
    }

    goToRequisitions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Requisitions"),
            res_model: "sales.requisition",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("sales_visit_plan.dashboard", VisitDashboard);
