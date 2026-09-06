import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Dialog } from "@web/core/dialog/dialog";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";

import { Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useRef, useState } from "@odoo/owl";

const PENDING_STATES = ["provisioning", "terminating"];
const POLL_MS = 5000;

/** Format a byte count as a short human string (decimal GB/MB/KB). */
export function formatBytes(bytes) {
    const b = bytes || 0;
    if (b >= 1e9) return (b / 1e9).toFixed(b >= 1e10 ? 0 : 1) + " GB";
    if (b >= 1e6) return (b / 1e6).toFixed(b >= 1e7 ? 0 : 1) + " MB";
    if (b >= 1e3) return Math.round(b / 1e3) + " KB";
    return b + " B";
}

// ─── Bilingual UI (English / العربية) — a live toggle, no page reload ───
export const LANGS = {
    en: {
        _dir: "ltr", _name: "English",
        tenants: "Tenants", new_tenant: "New Tenant",
        search_ph: "Search by name or company…",
        all: "All", active: "Active", not_active: "Not Active",
        of: "of", loading: "Loading…",
        no_tenants: "No tenants yet.",
        no_match: "No tenants match your search.",
        clear_filters: "Clear filters",
        stat_tenants: "Tenants", stat_active: "Active", stat_storage: "Storage used",
        near_full: "NEAR FULL", full: "FULL", per_mo: "/mo",
        over_limit: "OVER LIMIT — BLOCKED",
        work_in_progress: "Work in progress — this page refreshes itself.",
        st_draft: "Draft", st_provisioning: "Provisioning…", st_active: "Active",
        st_error: "Error", st_terminating: "Dropping…", st_terminated: "Terminated",
        used: "used", database: "Database", files: "Files", total_storage: "Total storage",
        storage_quota: "Storage quota", set_quota: "Set quota (GB)",
        save: "Save", saving: "Saving…",
        hint_full: "Out of storage — sell an upgrade (raise this tenant's quota).",
        hint_warn: "Nearly full — a good moment to offer an upgrade.",
        allocated_quota: "Allocated quota", rate: "Rate", invoice_mo: "Invoice / month",
        per_gb_mo: "/ GB / month",
        login: "Login", password: "Password",
        creds_note: "Hand this over once, then change it.",
        open: "Open", retry: "Retry", drop: "Drop", close: "Close",
        nt_name: "Tenant name",
        nt_name_help: "Becomes the database name and the subdomain.",
        nt_company: "Company (optional)", create: "Create", cancel: "Cancel",
    },
    ar: {
        _dir: "rtl", _name: "العربية",
        tenants: "العملاء", new_tenant: "عميل جديد",
        search_ph: "ابحث بالاسم أو الشركة…",
        all: "الكل", active: "نشط", not_active: "غير نشط",
        of: "من", loading: "جاري التحميل…",
        no_tenants: "لا يوجد عملاء بعد.",
        no_match: "لا يوجد عملاء مطابقون لبحثك.",
        clear_filters: "مسح الفلاتر",
        stat_tenants: "العملاء", stat_active: "النشطون", stat_storage: "المساحة المستخدمة",
        near_full: "قارب الامتلاء", full: "ممتلئ", per_mo: "/شهر",
        over_limit: "تخطّى الحد — موقوف",
        work_in_progress: "جاري العمل — الصفحة تُحدّث نفسها.",
        st_draft: "مسودة", st_provisioning: "جاري الإنشاء…", st_active: "نشط",
        st_error: "خطأ", st_terminating: "جاري الحذف…", st_terminated: "محذوف",
        used: "مستخدم", database: "قاعدة البيانات", files: "الملفات", total_storage: "إجمالي المساحة",
        storage_quota: "حصة التخزين", set_quota: "تحديد الحصة (GB)",
        save: "حفظ", saving: "جاري الحفظ…",
        hint_full: "المساحة امتلأت — اعرض ترقية (ارفع حصة هذا العميل).",
        hint_warn: "قاربت على الامتلاء — وقت مناسب لعرض ترقية.",
        allocated_quota: "الحصة المخصّصة", rate: "السعر", invoice_mo: "الفاتورة / شهر",
        per_gb_mo: "/ GB / شهر",
        login: "المستخدم", password: "كلمة المرور",
        creds_note: "سلّمها مرة واحدة، ثم غيّرها.",
        open: "فتح", retry: "إعادة المحاولة", drop: "حذف", close: "إغلاق",
        nt_name: "اسم العميل",
        nt_name_help: "يصبح اسم قاعدة البيانات والنطاق الفرعي.",
        nt_company: "الشركة (اختياري)", create: "إنشاء", cancel: "إلغاء",
    },
};

export function translate(lang, key) {
    return (LANGS[lang] || LANGS.en)[key] ?? (LANGS.en[key] ?? key);
}

export function loadLang() {
    try { return localStorage.getItem("msol_saas_lang") === "ar" ? "ar" : "en"; }
    catch (e) { return "en"; }
}

/**
 * Per-tenant storage doughnut (real usage): Database vs Files, with the total
 * in the centre. Uses Chart.js (web.chartjs_lib), the same library the core
 * graph view loads.
 */
export class TenantUsageChart extends Component {
    static template = "msolutions_saas.TenantUsageChart";
    static props = { db: Number, files: Number };

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;
        onMounted(() => this.renderChart());
        onWillUpdateProps((next) => this.renderChart(next));
        onWillUnmount(() => this.chart && this.chart.destroy());
    }

    get total() {
        return (this.props.db || 0) + (this.props.files || 0);
    }
    get totalLabel() {
        return formatBytes(this.total);
    }

    async renderChart(props = this.props) {
        await loadBundle("web.chartjs_lib");
        if (!this.canvasRef.el) {
            return;
        }
        const db = props.db || 0;
        const files = props.files || 0;
        const empty = db + files === 0;
        const data = empty ? [1] : [db, files];
        const colors = empty ? ["#e2e8f0"] : ["#2563eb", "#38bdf8"];
        if (this.chart) {
            this.chart.data.datasets[0].data = data;
            this.chart.data.datasets[0].backgroundColor = colors;
            this.chart.update();
            return;
        }
        // eslint-disable-next-line no-undef
        this.chart = new Chart(this.canvasRef.el, {
            type: "doughnut",
            data: {
                labels: ["Database", "Files"],
                datasets: [{ data, backgroundColor: colors, borderWidth: 0, hoverOffset: 3 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: "70%",
                animation: { duration: 300 },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: !empty,
                        callbacks: {
                            label: (ctx) => `${ctx.label}: ${formatBytes(ctx.parsed)}`,
                        },
                    },
                },
            },
        });
    }
}

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
        lang: String,
    };

    setup() {
        this.state = useState({ name: "", companyName: "", error: "" });
    }

    tr(key) { return translate(this.props.lang, key); }
    get dir() { return this.props.lang === "ar" ? "rtl" : "ltr"; }

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

/**
 * Per-tenant details wizard: the storage doughnut, the DB/Files breakdown, and
 * the live monthly price computed from real usage. Opened from a card's gear.
 */
export class TenantDetailsDialog extends Component {
    static template = "msolutions_saas.TenantDetailsDialog";
    static components = { Dialog, TenantUsageChart };
    static props = {
        close: Function,
        tenant: Object,
        pricing: Object,
        lang: String,
        onOpen: Function,
        onDrop: Function,
        onRetry: Function,
        onSaved: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        // Local, editable copy of the quota so the gauge moves live as you type.
        this.state = useState({ quota: this.props.tenant.quota_gb || 0, saving: false });
    }

    tr(key) { return translate(this.props.lang, key); }
    get dir() { return this.props.lang === "ar" ? "rtl" : "ltr"; }
    stateLabel() { return translate(this.props.lang, "st_" + this.t.state); }

    get t() { return this.props.tenant; }
    get dbBytes() { return this.t.disk_db_bytes || 0; }
    get fsBytes() { return this.t.disk_fs_bytes || 0; }
    get totalBytes() { return this.dbBytes + this.fsBytes; }
    fmt(bytes) { return formatBytes(bytes); }

    onQuotaInput(ev) { this.state.quota = ev.target.value; }
    get quotaDirty() {
        return (parseFloat(this.state.quota) || 0) !== (this.t.quota_gb || 0);
    }
    async saveQuota() {
        const q = parseFloat(this.state.quota) || 0;
        this.state.saving = true;
        try {
            await this.orm.write("saas.tenant", [this.t.id], { quota_gb: q });
            this.t.quota_gb = q;              // keep the passed object in sync
            this.notification.add(_t("Storage quota updated to %s GB.", q), { type: "success" });
            if (this.props.onSaved) { this.props.onSaved(); }
        } finally {
            this.state.saving = false;
        }
    }

    get quotaGb() { return parseFloat(this.state.quota) || 0; }
    get quotaBytes() { return this.quotaGb * 1e9; }
    get quotaPct() {
        if (this.quotaBytes <= 0) return this.totalBytes > 0 ? 100 : 0;
        return Math.min(100, (this.totalBytes / this.quotaBytes) * 100);
    }
    get quotaPctLabel() { return Math.round(this.quotaPct); }
    get quotaState() {
        const pct = this.quotaBytes > 0
            ? (this.totalBytes / this.quotaBytes) * 100
            : (this.totalBytes > 0 ? 101 : 0);
        return pct >= 100 ? "full" : pct >= 80 ? "warn" : "ok";
    }
    get quotaColor() {
        return { ok: "#16a34a", warn: "#f59e0b", full: "#e11d48" }[this.quotaState];
    }
    get quotaBarStyle() {
        return `width:${Math.max(2, this.quotaPct)}%;background:${this.quotaColor}`;
    }

    get p() { return this.props.pricing || {}; }
    get currency() { return this.p.currency || ""; }
    get totalGB() { return this.totalBytes / 1e9; }
    // Invoice = allocated quota × rate. quotaGb reads the (editable) state, so
    // the invoice updates live as you change the quota.
    get price() { return this.quotaGb * (this.p.per_gb || 0); }
    money(v) { return Math.round(v * 100) / 100; }
    gb(v) { return Math.round(v * 1000) / 1000; }

    doOpen() { this.props.onOpen(); this.props.close(); }
    doDrop() { this.props.close(); this.props.onDrop(); }
    doRetry() { this.props.onRetry(); this.props.close(); }
}

export class SaasDashboard extends Component {
    static template = "msolutions_saas.SaasDashboard";
    static components = { NewTenantDialog, TenantUsageChart, TenantDetailsDialog };
    static props = { ...standardActionServiceProps };

    // ---- Language (live EN/AR toggle) ----
    tr(key) { return translate(this.state.lang, key); }
    get dir() { return this.state.lang === "ar" ? "rtl" : "ltr"; }
    setLang(lang) {
        this.state.lang = lang;
        try { localStorage.setItem("msol_saas_lang", lang); } catch (e) {}
    }

    /** Human-readable byte size, for the storage labels. */
    formatBytes(bytes) {
        return formatBytes(bytes);
    }
    tenantTotalBytes(tenant) {
        return (tenant.disk_db_bytes || 0) + (tenant.disk_fs_bytes || 0);
    }

    // ---- Fleet KPIs (header stat tiles) ----
    get activeCount() {
        return this.state.tenants.filter((t) => t.state === "active").length;
    }
    get totalStorageBytes() {
        return this.state.tenants.reduce((s, t) => s + this.tenantTotalBytes(t), 0);
    }
    get totalRevenue() {
        return this.state.tenants
            .filter((t) => t.state === "active")
            .reduce((s, t) => s + this.tenantPrice(t), 0);
    }
    formatMoney(v) {
        const p = this.state.pricing || {};
        return `${Math.round((v || 0) * 100) / 100} ${p.currency || ""}`.trim();
    }

    /** Colour of the small state pip on the compact card. */
    statePipColor(tenant) {
        return {
            active: "#16a34a",
            error: "#e11d48",
            provisioning: "#f59e0b",
            terminating: "#f59e0b",
            terminated: "#94a3b8",
            draft: "#94a3b8",
        }[tenant.state] || "#94a3b8";
    }

    /** Storage-quota gauge helpers (used vs the tenant's GB allowance). */
    quotaBytes(tenant) {
        return (tenant.quota_gb || 0) * 1e9;
    }
    quotaPct(tenant) {
        const q = this.quotaBytes(tenant);
        const used = this.tenantTotalBytes(tenant);
        if (q <= 0) return used > 0 ? 100 : 0;   // no allowance + any usage = full
        return Math.min(100, (used / q) * 100);
    }
    quotaState(tenant) {
        const q = this.quotaBytes(tenant);
        const used = this.tenantTotalBytes(tenant);
        const pct = q > 0 ? (used / q) * 100 : (used > 0 ? 101 : 0);
        if (pct >= 100) return "full";
        if (pct >= 80) return "warn";
        return "ok";
    }
    quotaColor(tenant) {
        return { ok: "#16a34a", warn: "#f59e0b", full: "#e11d48" }[this.quotaState(tenant)];
    }
    quotaBarStyle(tenant) {
        return `width:${Math.max(2, this.quotaPct(tenant))}%;background:${this.quotaColor(tenant)}`;
    }

    /** Monthly invoice: the allocated quota (GB) × the per-GB rate. */
    tenantPrice(tenant) {
        const p = this.state.pricing || {};
        return (tenant.quota_gb || 0) * (p.per_gb || 0);
    }
    formatPrice(tenant) {
        const p = this.state.pricing || {};
        const num = Math.round(this.tenantPrice(tenant) * 100) / 100;
        return `${num} ${p.currency || ""}`.trim();
    }

    /** Open the per-tenant details wizard (chart + breakdown + pricing). */
    openDetails(tenant) {
        this.dialog.add(TenantDetailsDialog, {
            tenant,
            pricing: this.state.pricing,
            lang: this.state.lang,
            onOpen: () => window.open(tenant.url, "_blank"),
            onDrop: () => this.onDrop(tenant),
            onRetry: () => this.onRetry(tenant),
            onSaved: () => this.load(),
        });
    }

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            tenants: [],
            baseDomain: "",
            pricing: {},
            loading: true,
            query: "",
            filter: "all",   // "all" | "active" | "inactive"
            lang: "en",      // admin console stays English; the customer-facing
                             // pages (404 / quota-exceeded) are the bilingual ones
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
        this.state.pricing = data.pricing || {};
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
        return this.tr("st_" + tenant.state);
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
            lang: this.state.lang,
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
