import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

class SaasLogsField extends Component {
    static template = "msolutions_saas.SaasLogs";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            data: null,
            loading: true,
            error: null,
            activeTab: "activity",   // "activity" | "slow" | "locks"
        });
        onWillStart(() => this.load());
    }

    get tenantId() {
        return this.props.value;
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call(
                "saas.tenant", "get_db_activity", [[this.tenantId]]
            );
        } catch (err) {
            this.state.error = err.data?.message || err.message || String(err);
        } finally {
            this.state.loading = false;
        }
    }

    setTab(t) { this.state.activeTab = t; }

    /** Format seconds → "1h 23m 04s" style. */
    formatDuration(sec) {
        if (sec === null || sec === undefined) return "—";
        const s = Math.abs(Math.round(Number(sec)));
        if (s < 60) return `${s}s`;
        if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
        return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
    }

    stateClass(state) {
        return {
            active: "o_saas_logs_state_active",
            idle: "o_saas_logs_state_idle",
            "idle in transaction": "o_saas_logs_state_idle_tx",
        }[state] || "o_saas_logs_state_other";
    }
}

registry.category("fields").add("saas_logs", {
    component: SaasLogsField,
    supportedTypes: ["integer"],
    readonly: true,
});
