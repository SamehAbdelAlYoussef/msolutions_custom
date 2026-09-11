/** @odoo-module **/
/*
 * msolutions - Community-compatible accounting distribution.
 *
 * `AccountReport` renders an `account.report` produced by the Community report
 * engine (`account.report.get_report_data`). Enterprise ships this component in
 * account_reports; this is the Community replacement.
 *
 * The component is intentionally data-driven: everything it draws comes from
 * the `options` / `lines` payload, so any report defined in the database is
 * rendered without report-specific code.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ControlPanel } from "@web/search/control_panel/control_panel";

export class AccountReport extends Component {
    static template = "account_reports.AccountReport";
    static props = { "*": true };
    static components = { ControlPanel };

    static _customComponents = {};

    static registerCustomComponent(component) {
        if (component && component.name) {
            AccountReport._customComponents[component.name] = component;
        }
    }

    static getCustomComponent(name) {
        return AccountReport._customComponents[name] || null;
    }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            error: false,
            lines: [],
            options: {},
            unfolded: [],
            unfoldAll: false,
        });
        onWillStart(() => this.load());
    }

    get reportId() {
        const action = this.props.action || {};
        const params = action.params || {};
        const context = action.context || {};
        return params.report_id || context.report_id || false;
    }

    get columns() {
        return this.state.options.columns || [];
    }

    get dateFrom() {
        return (this.state.options.date || {}).date_from || "";
    }

    get dateTo() {
        return (this.state.options.date || {}).date_to || "";
    }

    async load(extra) {
        if (!this.reportId) {
            this.state.loading = false;
            this.state.error = true;
            return;
        }
        this.state.loading = true;
        this.state.error = false;
        const previous = {
            unfolded_lines: this.state.unfolded,
            unfold_all: this.state.unfoldAll,
        };
        // Only send a date range once we have one: an empty `date_from` would be
        // parsed server side. On the first load we let the server pick the
        // default period from the report's `default_opening_date_filter`.
        const current = this.state.options.date;
        const date =
            (extra && extra.date) ||
            (current && current.date_from && current.date_to ? current : null);
        if (date) {
            previous.date = date;
        }
        try {
            const data = await this.orm.call("account.report", "get_report_data", [this.reportId], {
                previous_options: previous,
            });
            this.state.options = data.options || {};
            this.state.lines = data.lines || [];
        } catch (error) {
            this.state.error = true;
            if (this.notification) {
                this.notification.add(error.message || String(error), { type: "danger" });
            }
        }
        this.state.loading = false;
    }

    async onDateChange(ev) {
        const date = Object.assign({}, this.state.options.date);
        date[ev.target.name] = ev.target.value;
        date.filter = "custom";
        await this.load({ date });
    }

    async toggleLine(line) {
        if (!line.unfoldable) {
            return;
        }
        const unfolded = new Set(this.state.unfolded);
        if (unfolded.has(line.id)) {
            unfolded.delete(line.id);
        } else {
            unfolded.add(line.id);
        }
        this.state.unfolded = [...unfolded];
        await this.load();
    }

    async toggleUnfoldAll() {
        this.state.unfoldAll = !this.state.unfoldAll;
        await this.load();
    }

    /** Row classes, mirroring Enterprise (`line_level_N`, `unfolded`, `total`). */
    lineClasses(line) {
        const classes = [`line_level_${line.level}`];
        if (line.unfolded) {
            classes.push("unfolded");
        }
        if (line.level === 0) {
            classes.push("total");
        }
        return classes.join(" ");
    }
}

if (!registry.category("actions").contains("account_report")) {
    registry.category("actions").add("account_report", AccountReport);
}

export default AccountReport;
