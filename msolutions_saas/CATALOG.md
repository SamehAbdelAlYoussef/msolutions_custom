# MSolutions SaaS — Feature Catalog

Control-plane module for running an Odoo 19 multi-tenant SaaS: one PostgreSQL
database per tenant, one subdomain per tenant, managed entirely from a backend
dashboard. Module version **19.0.14.x**.

---

## 1. Tenant provisioning

| Capability | What it does |
|---|---|
| **Template-based provisioning** | A new tenant is a **file-level clone** of a prebuilt template database → ready in **~1 second** instead of ~1–8 minutes. |
| **From-scratch fallback** | If a plan has no template (or it is missing) the tenant is built from scratch. A missing template never breaks provisioning. |
| **Default Plan** | New tenants automatically use the configured Default Plan (e.g. *Basic* → `tpl_basic`) so a dashboard "Create" clones instantly, with no extra clicks. |
| **Delta app install** | The template carries the standard apps; any app a tenant wants **beyond** the template is installed on top, on demand (and takes its own time). |
| **Correct ownership split** | Every tenant DB is owned by `odoo_provision`; its objects by `odoo_web` — reproduced automatically on every clone. |

**Plans:** `SaaS → Plans`. Each plan has a template database (`tpl_*`), its module
set, and a "template age" indicator (rebuild when stale).

**Rebuild templates:** `/opt/scripts/rebuild_templates.sh` — drops, recreates,
installs each plan's modules, bakes the ownership split, marks the DB as a
template. Run after any Odoo upgrade or module change.

---

## 2. Tenant management

| Capability | What it does |
|---|---|
| **Auto-discovery** | Any tenant database that exists on the server automatically appears in **Tenant Records** — including ones created before this module. Runs every 5 min (watchdog) + a **"Sync from Server"** button. |
| **Complete deletion (no trace)** | Drop removes the **database + filestore + the record** — nothing is left behind (the audit log keeps the history). |
| **Safety backup before drop** | A full DB + filestore backup is taken before any drop (swept to Backblaze B2, auto-expires). |
| **Stuck-tenant watchdog** | A tenant stuck mid-provision is moved to *error* after a timeout so it can be retried. |

---

## 3. Storage, quotas & pricing

| Capability | What it does |
|---|---|
| **Real usage** | Each card shows the tenant's **real** disk usage — database size (`pg_database_size`) + filestore size (`du`). No estimates. |
| **Usage breakdown** | The details wizard (⚙️) shows a doughnut of **Database vs Files** with the total in the centre. |
| **Per-tenant storage quota (GB)** | Every tenant has a storage allowance, editable per customer from the ⚙️ wizard (live) or the Tenant Records form. |
| **Used / quota gauge** | A colour bar (green → amber → red) shows how full each tenant is; **near-full / FULL** flags mark the upsell moment. |
| **Usage-based pricing** | Monthly invoice = **quota (GB) × price per GB**. Set the rate in Configuration; the invoice moves as you raise a tenant's quota. |

**Configuration:** `SaaS → Configuration`
- Default Plan, Default Storage Quota (GB)
- Currency, Price / GB / month
- Default apps for from-scratch tenants

---

## 4. Quota enforcement (customer suspension)

When a tenant exceeds its quota it is **suspended** — the reverse proxy serves a
"storage full" page instead of the tenant, until the quota is raised.

| Part | Behaviour |
|---|---|
| **Trigger** | Immediate when you change a quota (~2 s); a 5-min cron catches organic growth. |
| **Dashboard** | A red **"OVER LIMIT — BLOCKED"** banner on the card. |
| **Customer page** | A professional bilingual (EN / ع) **"Storage is full"** page: a 100 % gauge, a "Go to MSolutions" button, and a **WhatsApp "Contact us to upgrade"** button (`+20 101 772 9427`). |
| **Real block** | Served by nginx at the gateway (`HTTP 402`) — the customer cannot reach Odoo at all; no bypass. |
| **Reversible** | Raise the quota → unblocked within ~2 s. **No customer database is ever touched**; the data is safe. |
| **No per-DB module** | Everything runs at the control plane + nginx level. |

---

## 5. Dashboard & customer pages

- **Tenants dashboard** (`SaaS → Tenants`): KPI tiles (tenants / active / storage
  used), compact cards (4 per row), search + filters, a per-tenant **details
  wizard** (⚡ chart, quota editor, invoice, credentials, Open / Drop / Retry).
- **Custom 404 page** (`tenant_not_found.html`): bilingual "Workspace not found".
- **Storage-full page** (`quota_exceeded.html`): bilingual, with WhatsApp contact.

---

## 6. Infrastructure & operations

| Area | Notes |
|---|---|
| **Subdomain routing** | nginx wildcard + a live allowlist (`tenants.conf`) rebuilt from the database. New tenant is reachable within **~2 s** of provisioning. |
| **Suspension routing** | `suspended.conf` + a `$suspended_tenant` map → `HTTP 402` upgrade page. |
| **Fast sync** | `/opt/scripts/update_tenant_list.sh` on a 2-second systemd timer, reloads nginx only when the list actually changes. |
| **Security** | Split PostgreSQL roles (`odoo_web` non-superuser, `odoo_provision` CREATEDB); credentials in `.pgpass` (600); the superuser is unused and its password rotated; the module never opens a tenant registry — raw psycopg only. |
| **Backups** | Nightly `pg_dump` + filestore → restic → Backblaze B2, with a pre-drop safety copy. |

---

## Operator quick reference

| Task | Where |
|---|---|
| Create a tenant | Tenants → New Tenant (uses the Default Plan → seconds) |
| See a tenant's storage / invoice | Tenants → ⚙️ on the card |
| Give a customer more space | ⚙️ → set quota (GB) → Save (unblocks instantly if suspended) |
| Import existing databases | Tenants → Sync from Server |
| Set the price / GB, default quota | Configuration |
| Add apps to a plan | Plans → edit modules → run `rebuild_templates.sh` |
| Change the WhatsApp upgrade number | `/opt/odoo/html/quota_exceeded.html` → `WHATSAPP` |
