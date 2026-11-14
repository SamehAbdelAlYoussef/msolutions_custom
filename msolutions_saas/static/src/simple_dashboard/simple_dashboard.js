/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * SaaS dashboard. One question: how are my customers split between the Active
 * container and the Demo container, and which demos need me today?
 */
export class SimpleDashboard extends Component {
    static template = "msolutions_saas.SimpleDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, busy: 0 });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("saas.tenant", "simple_dashboard_data", []);
    }

    get d() {
        return this.state.data || {};
    }

    get total() {
        return (this.d.active || 0) + (this.d.demo || 0);
    }

    /** Width of each side of the split bar; an empty fleet shows an even bar. */
    get activePct() {
        return this.total ? Math.round(((this.d.active || 0) / this.total) * 100) : 50;
    }

    get attention() {
        const d = this.d;
        return [
            { key: "ending", tone: "warn", n: d.ending, text: "demos end within 3 days" },
            { key: "expired", tone: "bad", n: d.expired, text: "demos have expired" },
            { key: "errors", tone: "bad", n: d.errors, text: "customers failed to provision" },
        ].filter((a) => a.n);
    }

    daysLabel(row) {
        if (row.expired) return "Expired";
        if (row.days_left === 0) return "Ends today";
        return row.days_left === 1 ? "1 day left" : `${row.days_left} days left`;
    }

    daysTone(row) {
        if (row.expired) return "bad";
        return row.days_left <= 3 ? "warn" : "calm";
    }

    newCustomer(active) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: active ? "New active customer" : "New demo customer",
            res_model: "saas.tenant",
            views: [[false, "form"]],
            target: "current",
            context: { default_is_active_customer: active },
        });
    }

    openCustomer(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "saas.tenant",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openList(kind) {
        if (kind === "errors") {
            return this.action.doAction({
                type: "ir.actions.act_window", name: "Failed to provision",
                res_model: "saas.tenant", views: [[false, "list"], [false, "form"]],
                domain: [["state", "=", "error"]],
            });
        }
        return this.action.doAction(
            kind === "active"
                ? "msolutions_saas.action_saas_customers_active"
                : "msolutions_saas.action_saas_customers_trial"
        );
    }

    async makeActive(ev, row) {
        ev.stopPropagation();
        this.state.busy = row.id;
        try {
            await this.orm.write("saas.tenant", [row.id], { is_active_customer: true });
            await this.load();
        } finally {
            this.state.busy = 0;
        }
    }
}

registry.category("actions").add("msolutions_saas.simple_dashboard", SimpleDashboard);
