import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Dialog } from "@web/core/dialog/dialog";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";

const PENDING_STATES = ["provisioning", "terminating"];
const POLL_MS = 5000;

/**
 * Name/company prompt for a new tenant. Validation is duplicated from
 * saas.tenant._check_name so the user gets the message before a round trip;
 * the server constraint is still the one that decides.
 */
export class NewTenantDialog extends Component {
    static template = "msolutions_saas.NewTenantDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        onConfirm: Function,
        baseDomain: String,
    };

    setup() {
        this.state = useState({ name: "", companyName: "", error: "" });
    }

    get preview() {
        return this.state.name
            ? `https://${this.state.name}.${this.props.baseDomain}`
            : "";
    }

    onNameInput(ev) {
        this.state.name = ev.target.value.trim().toLowerCase();
        this.state.error = "";
    }

    async confirm() {
        const name = this.state.name;
        if (!/^[a-z][a-z0-9]{2,30}$/.test(name)) {
            this.state.error = _t(
                "Use 4 to 31 characters: lowercase letters and digits only, starting with a letter."
            );
            return;
        }
        await this.props.onConfirm(name, this.state.companyName);
        this.props.close();
    }
}

export class SaasDashboard extends Component {
    static template = "msolutions_saas.SaasDashboard";
    static components = { NewTenantDialog };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            tenants: [],
            baseDomain: "",
            loading: true,
            query: "",
            filter: "all",   // "all" | "active" | "inactive"
        });

        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => this.poll(), POLL_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load() {
        const data = await this.orm.call("saas.tenant", "dashboard_data", []);
        this.state.baseDomain = data.base_domain;
        this.state.tenants = data.tenants;
        this.state.loading = false;
    }

    /** Only refresh while something is actually moving. */
    poll() {
        if (this.state.tenants.some((t) => PENDING_STATES.includes(t.state))) {
            this.load();
        }
    }

    // ----------------------------------------------------------------
    // Search + filter (client-side — all tenants are already in memory)
    // ----------------------------------------------------------------

    get visibleTenants() {
        let list = this.state.tenants;

        if (this.state.filter === "active") {
            list = list.filter((t) => t.state === "active");
        } else if (this.state.filter === "inactive") {
            list = list.filter((t) => t.state !== "active");
        }

        const q = this.state.query.trim().toLowerCase();
        if (q) {
            list = list.filter(
                (t) =>
                    t.name.toLowerCase().includes(q) ||
                    (t.company_name && t.company_name.toLowerCase().includes(q))
            );
        }

        return list;
    }

    get isFiltered() {
        return this.state.filter !== "all" || this.state.query.trim() !== "";
    }

    onSearch(ev) {
        this.state.query = ev.target.value;
    }

    clearSearch() {
        this.state.query = "";
    }

    setFilter(f) {
        this.state.filter = f;
    }

    // ----------------------------------------------------------------
    // Display helpers
    // ----------------------------------------------------------------

    get hasPending() {
        return this.state.tenants.some((t) => PENDING_STATES.includes(t.state));
    }

    stateLabel(tenant) {
        return {
            draft: _t("Draft"),
            provisioning: _t("Provisioning…"),
            active: _t("Active"),
            error: _t("Error"),
            terminating: _t("Dropping…"),
            terminated: _t("Terminated"),
        }[tenant.state];
    }

    /** CSS class on the card wrapper — drives the left accent border colour. */
    cardClass(tenant) {
        return {
            "o_saas_card": true,
            "h-100": true,
            "o_saas_card_active":     tenant.state === "active",
            "o_saas_card_error":      tenant.state === "error",
            "o_saas_card_pending":    ["provisioning", "terminating"].includes(tenant.state),
            "o_saas_card_terminated": tenant.state === "terminated",
            "o_saas_card_draft":      tenant.state === "draft",
        };
    }

    /** CSS class on the state badge pill. */
    badgeClass(tenant) {
        return {
            "o_saas_state_badge": true,
            "o_saas_badge_active":     tenant.state === "active",
            "o_saas_badge_error":      tenant.state === "error",
            "o_saas_badge_pending":    ["provisioning", "terminating"].includes(tenant.state),
            "o_saas_badge_terminated": tenant.state === "terminated",
            "o_saas_badge_draft":      tenant.state === "draft",
        };
    }

    // Keep old stateClass for any remaining references
    stateClass(tenant) {
        return this.badgeClass(tenant);
    }

    // ----------------------------------------------------------------
    // Actions
    // ----------------------------------------------------------------

    onNewTenant() {
        this.dialog.add(NewTenantDialog, {
            baseDomain: this.state.baseDomain,
            onConfirm: async (name, companyName) => {
                await this.orm.call("saas.tenant", "create_tenant", [name, companyName]);
                this.notification.add(
                    _t("Creating %s. This takes about a minute.", name),
                    { type: "info" }
                );
                await this.load();
            },
        });
    }

    onDrop(tenant) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Drop tenant"),
            body: _t(
                "This permanently deletes the database '%s' and its filestore. " +
                    "There is no backup and this cannot be undone.",
                tenant.name
            ),
            confirmLabel: _t("Drop it"),
            confirmClass: "btn-danger",
            cancel: () => {},
            confirm: async () => {
                await this.orm.call("saas.tenant", "action_drop", [[tenant.id]]);
                this.notification.add(_t("Dropping %s.", tenant.name), { type: "info" });
                await this.load();
            },
        });
    }

    async onRetry(tenant) {
        await this.orm.call("saas.tenant", "action_provision", [[tenant.id]]);
        await this.load();
    }
}

registry.category("actions").add("msolutions_saas.dashboard", SaasDashboard);
