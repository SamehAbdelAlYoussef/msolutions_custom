/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Very simple SaaS dashboard: a few counters, each opens the matching list. */
export class SimpleDashboard extends Component {
    static template = "msolutions_saas.SimpleDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null });
        onWillStart(async () => {
            this.state.data = await this.orm.call("saas.tenant", "simple_dashboard_data", []);
        });
    }

    get cards() {
        const d = this.state.data || {};
        return [
            { key: "active", label: "Active Customers", value: d.active, tone: "ok", go: "active" },
            { key: "trial", label: "Trial Customers", value: d.trial, tone: "info", go: "trial" },
            { key: "ending", label: "Trials ending in 3 days", value: d.trial_ending, tone: "warn", go: "ending" },
            { key: "expired", label: "Expired trials", value: d.trial_expired, tone: "bad", go: "expired" },
            { key: "problems", label: "Provisioning errors", value: d.problems, tone: "bad", go: "problems" },
        ];
    }

    open(card) {
        const base = { type: "ir.actions.act_window", res_model: "saas.tenant",
                       views: [[false, "list"], [false, "form"]] };
        switch (card.go) {
            case "active":
                return this.action.doAction("msolutions_saas.action_saas_customers_active");
            case "trial":
                return this.action.doAction("msolutions_saas.action_saas_customers_trial");
            case "ending":
                return this.action.doAction({ ...base, name: card.label,
                    domain: [["is_active_customer", "=", false], ["trial_expired", "=", false]] });
            case "expired":
                return this.action.doAction({ ...base, name: card.label,
                    domain: [["trial_expired", "=", true]] });
            default:
                return this.action.doAction({ ...base, name: card.label,
                    domain: [["state", "=", "error"]] });
        }
    }
}

registry.category("actions").add("msolutions_saas.simple_dashboard", SimpleDashboard);
