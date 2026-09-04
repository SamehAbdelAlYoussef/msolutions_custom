import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

class SaasShellField extends Component {
    static template = "msolutions_saas.SaasShell";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            query: "",
            result: null,
            error: null,
            running: false,
            history: [],       // last 20 executed queries
            historyIndex: -1,  // for up/down arrow navigation
        });
    }

    /** The tenant record ID comes through as the field value (field name = id). */
    get tenantId() {
        return this.props.value;
    }

    async run() {
        const q = this.state.query.trim();
        if (!q || this.state.running) return;

        this.state.running = true;
        this.state.result = null;
        this.state.error = null;

        try {
            const res = await this.orm.call(
                "saas.tenant",
                "shell_execute",
                [[this.tenantId], q],
            );
            this.state.result = res;
            // Prepend to history, keep unique, cap at 20
            this.state.history = [q, ...this.state.history.filter((h) => h !== q)].slice(0, 20);
            this.state.historyIndex = -1;
        } catch (err) {
            this.state.error = err.data?.message || err.message || String(err);
        } finally {
            this.state.running = false;
        }
    }

    onKeydown(ev) {
        // Ctrl/Cmd + Enter → run
        if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
            ev.preventDefault();
            this.run();
            return;
        }
        // Arrow Up → previous history entry
        if (ev.key === "ArrowUp" && this.state.history.length) {
            ev.preventDefault();
            const next = Math.min(this.state.historyIndex + 1, this.state.history.length - 1);
            this.state.historyIndex = next;
            this.state.query = this.state.history[next];
        }
        // Arrow Down → next history entry
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            const next = this.state.historyIndex - 1;
            if (next < 0) {
                this.state.historyIndex = -1;
                this.state.query = "";
            } else {
                this.state.historyIndex = next;
                this.state.query = this.state.history[next];
            }
        }
    }

    onInput(ev) {
        this.state.query = ev.target.value;
        this.state.historyIndex = -1;
    }

    clearResult() {
        this.state.result = null;
        this.state.error = null;
    }

    get statusLine() {
        const r = this.state.result;
        if (!r) return "";
        if (r.columns.length) {
            const suffix = r.truncated ? _t(" (showing first 500 rows)") : "";
            return _t("%s row(s) returned%s", r.rows.length, suffix);
        }
        return _t("%s row(s) affected", r.rowcount >= 0 ? r.rowcount : "?");
    }
}

registry.category("fields").add("saas_shell", {
    component: SaasShellField,
    supportedTypes: ["integer"],
    readonly: true,
});
