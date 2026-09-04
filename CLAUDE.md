# CLAUDE.md — msolutions_custom (Odoo 19 multi-tenant SaaS)

Read this before touching anything. Full build: **`docs/INFRASTRUCTURE.md`**.

## What this system is
A multi-tenant Odoo 19 **Community** SaaS. **One PostgreSQL database per tenant.**
`tenant name = database name = subdomain` (`<name>.msolutions-eg.com`). A
**control-plane database** (`msolutions`) runs the `msolutions_saas` module, which
provisions and drops tenant databases. Postgres runs on a **separate host** from
Odoo; there is no private link between them (public→public over TLS).

## Hard rules — do not break these
1. **Never open a tenant Registry or use the ORM against a tenant database.** Use
   raw psycopg via `odoo.sql_db.db_connect(<db>)` inside `closing()`, released
   immediately. Each registry load costs 150–300 MB of worker RAM — this is the
   binding capacity limit.
2. **Enumerate tenants dynamically, never hardcode a list.** Everything must work
   identically at 3 tenants and at 300. Enumerate by CONNECT privilege
   (`has_database_privilege`), not by a static list.
3. **The internet-facing containers must never hold DROP-database privilege.**
   `odoo_web` (web/gevent) = `NOCREATEDB`; only `odoo_worker` connects as
   `odoo_provision` (`CREATEDB`). Do not "simplify" to one role.
4. **The database is owned by `odoo_provision`; schema objects by `odoo_web`.**
   Use `ALTER ... OWNER`, **never `REASSIGN OWNED`** (it also flips the database
   owner and silently lets the web tier drop databases). See §4.
5. **B2 / restic credentials must never be reachable from an Odoo container.**
   They live only in `/opt/scripts/backup.env` (root, 600) on the Odoo host.
   Object Lock stops deletion, not reading.
6. **The server pulls; it does not push.** Code comes from local → GitHub → CI.
   A hotfix edited on prod must be carried back via patch (see §13 THE PUSH GAP)
   or the next deploy silently reverts it.
7. **Odoo Community only.** No Enterprise / OEEL-licensed / license-bypass
   modules. Check every manifest's `license`.
8. **Never make a tenant read-only or suspend it over a quota.** Soft limits only.
9. **`db_maxconn` is small (8) and pgbouncer pools are per (database,user).**
   Adding tenants multiplies server-side connections. Do the arithmetic (§6).

## Counter-intuitive things you will be tempted to "fix" and must not
- `odoo_gevent` connects to Postgres **directly (5432), bypassing pgbouncer** —
  because LISTEN/NOTIFY needs a persistent session and transaction pooling kills
  the bus. Only `odoo_web` goes through pgbouncer (6432).
- Backups `pg_dump` as **`odoo_web` (the less-privileged role)** on purpose: it
  can only read what it owns, so a dump fails loudly the moment ownership drifts.
  `odoo_provision` would dump successfully while hiding the drift.
- Editing a **single bind-mounted file** (`nginx.conf`, `odoo-*.conf`) with
  `cp`/`sed -i` changes its inode and the container keeps the old file. Edit in
  place with `cat > file`, verify the inode inside the container, then reload.
- Dumps are `-Fp` (plain), **not `-Fc`** — compressed dumps defeat restic dedup.

## Topology (detail in §2)
```
Internet ─▶ nginx :443 (Odoo host, <ODOO_HOST_IP>)
             ├─ /websocket, /longpolling ─▶ odoo_gevent:8072 ─┐
             └─ everything else          ─▶ odoo_web:8069  ─┐ │
                                            odoo_worker (cron, no ports)
                                                           │ │
   odoo_web ─▶ pgbouncer :6432 (txn pool) ─┐               │ │
   odoo_gevent ─────────────────────────── ┤ TLS, /32 hba  ▼ ▼
   odoo_worker ──────────── :5432 direct ──┴─▶ PostgreSQL 16 (DB host, <DB_HOST_IP>)
                                               roles: odoo_web (no createdb),
                                                      odoo_provision (createdb)
   backups: Odoo host ─▶ pg_dump+filestore ─▶ local 7d ─▶ restic ─▶ Backblaze B2
```

## Where things live
- Odoo host `<ODOO_HOST_IP>`: `/opt/odoo` (compose, configs, nginx, filestore),
  `/opt/scripts` (backups), `/opt/odoo/msolutions_custom` (this repo, deployed).
- DB host `<DB_HOST_IP>`: `/opt/docker` (postgres + pgbouncer compose), reached
  from the Odoo host via `sshpass -f /root/.dbpass ssh root@<DB_HOST_IP>`.
- Control-plane DB: `msolutions`. Login `admin` at `https://msolutions-eg.com`.

## Before you change anything
- Verify claims against the box; this doc and `/home/dev/audit/hardening-log.md`
  are the source of record but reality wins.
- One commit per logical change. Scan every diff for secrets before committing.
- `.gitignore` must cover `backup.env`, `secrets.env`, `*.bak-*`, `__pycache__`,
  `*.pyc`. Never commit an IP or a credential.
