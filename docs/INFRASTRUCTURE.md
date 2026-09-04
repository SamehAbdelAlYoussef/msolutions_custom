# INFRASTRUCTURE.md — Odoo 19 Multi-Tenant SaaS

Authoritative build document. Everything here was re-derived from the running
system and `/home/dev/audit/hardening-log.md`, not from memory. Where a fact
could not be verified live it is marked `⚠️ UNVERIFIED`.

**Placeholders** (this document lives in a public repo — real values never appear):
`<ODOO_HOST_IP>` the Odoo host · `<DB_HOST_IP>` the PostgreSQL host ·
`<B2_KEY_ID>` / `<B2_APP_KEY>` Backblaze keys · `<RESTIC_PASSWORD>` ·
`<PG_SUPERUSER_PW>` · `<PGBOUNCER_AUTH_PW>` · `<ADMIN_PASSWD>` Odoo master pw.
Substitute the real value (from the secrets store, §14) before running any command.

Verified software versions: Odoo `19.0-20260630` (image `odoo:19`), PostgreSQL 16
(`postgres:16`), pgbouncer (`edoburu/pgbouncer`), restic `0.16.4`, `nginx:alpine`,
`redis:7-alpine`.

---

## 1. WHAT THIS IS

A SaaS that sells Odoo instances. Each customer ("tenant") gets:

- one **PostgreSQL database**, named exactly as the tenant (e.g. `sameh`);
- one **subdomain**, `<name>.msolutions-eg.com`, served by the same Odoo cluster;
- an isolated **filestore** directory under the shared data dir.

The identity is deliberately collapsed: **tenant name = database name = DNS label**.
That is why the name must satisfy the *stricter* of the two rule sets
(`^[a-z][a-z0-9]{2,30}$`, see §11).

Routing is data-driven, not per-tenant config: Odoo's `dbfilter = ^%d$` maps the
first hostname label to a database, and a wildcard TLS cert covers every
subdomain. So "provision a tenant" reduces to: **create the database, install
`base`, set admin credentials** — plus hand object ownership to the web role (§4)
and add the subdomain to nginx's allow-map (§7).

One database — **`msolutions`** — is the **control plane**. It runs the
`msolutions_saas` module (§11): a backend screen + cron that creates and drops the
*other* databases. The control plane is a tenant-shaped database itself, so a
guard (`_assert_control_plane`) stops the module operating anywhere else.

Why one-DB-per-tenant (not one DB, many companies): hard data isolation, per-tenant
backup/restore/drop, and the ability to move a tenant to another cluster by moving
one database. The cost is RAM — each live database is a registry (§16).

---

## 2. TOPOLOGY

Two hosts. **No private network between them** — Odoo reaches Postgres over the
public internet, secured by TLS + a `/32` `pg_hba` rule + a firewall (§5). This is
the #1 scaling weakness (§15).

```
                              INTERNET
                                 │  :80/:443  (only public surface)
                                 ▼
        ┌───────────────────────────────────────────── ODOO HOST <ODOO_HOST_IP> ┐
        │  odoo_nginx (nginx:alpine)  ports 80,443 published                     │
        │     ├─ location /websocket, /longpolling ─▶ odoo_gevent:8072           │
        │     ├─ location ~ /web/database/*         ─▶ return 404                 │
        │     └─ location /                         ─▶ odoo_web:8069              │
        │                                                                        │
        │  odoo_web    (odoo:19)  127.0.0.1:8069  HTTP, workers=4, cron=0        │
        │  odoo_gevent (odoo:19)  127.0.0.1:8072  websocket bus, cron=0          │
        │  odoo_worker (odoo:19)  NO published ports  --no-http, cron=1          │
        │  odoo_redis  (redis:7)  6379 internal  (running; see note)             │
        │  shared bind mounts: ./filestore, ./config/*.conf, ./msolutions_custom │
        └──────────┬───────────────────┬───────────────────┬────────────────────┘
                   │ 6432 (pgbouncer)   │ 5432 direct       │ 5432 direct
                   │ user=odoo_web      │ user=odoo_web     │ user=odoo_provision
                   │ (odoo_web tier)    │ (odoo_gevent)     │ (odoo_worker)
                   ▼                    ▼                   ▼
        ┌───────────────────────────────────────────── DB HOST <DB_HOST_IP> ─────┐
        │  pgbouncer (edoburu)  0.0.0.0:6432  transaction pool, client TLS       │
        │        └─▶ db:5432 (auth via pgbouncer_auth, auth_dbname=postgres)     │
        │  odoo_db  (postgres:16)  0.0.0.0:5432  ssl=on                          │
        │        roles: odoo (super), odoo_provision (createdb),                 │
        │               odoo_web (no createdb), pgbouncer_auth                   │
        │  minio_storage (minio)  127.0.0.1:9000/9001  (present; not in dump path)│
        │  firewall: DOCKER-USER drops :5432/:6432 from any source ≠ <ODOO_HOST_IP>│
        └────────────────────────────────────────────────────────────────────────┘

   BACKUPS (Odoo host):  pg_dump -Fp + filestore.tar.gz
        ─▶ /opt/backups/local/<date>  (7-day local tier)
        ─▶ restic ─▶ s3:Backblaze B2 (eu-central-003)  (7 daily/4 weekly/3 monthly)
```

**Internet-facing:** only `odoo_nginx` :80/:443 on the Odoo host. Everything else
binds to `127.0.0.1` or an internal Docker network. Postgres :5432 and pgbouncer
:6432 listen on `0.0.0.0` **but** the DB-host firewall drops them from every
source except `<ODOO_HOST_IP>` (defense beyond the `/32` hba rule).

> ⚠️ **redis**: `odoo_redis` runs (`--maxmemory 2gb allkeys-lru`) but **no Odoo
> config references it** — none of the three `odoo-*.conf` files set a redis
> session/cache backend. Treat it as present-but-unwired until that changes.

---

## 3. THE THREE-CONTAINER SPLIT — and why

All three run the same `odoo:19` image and the same code mount; they differ only
in config file and command. Defined in `/opt/odoo/docker-compose.yml`.

**The reason:** the containers that answer the internet must not hold the
privileges that create and drop databases. A web-tier RCE or SQL-injection then
cannot `DROP DATABASE`. So the HTTP tiers connect as `odoo_web` (no CREATEDB); the
single non-published worker connects as `odoo_provision` (CREATEDB) and is the only
place provisioning/drops execute.

| container | config (`/opt/odoo/config/…`) | db_port | db_user | workers | max_cron_threads | http | published ports |
|---|---|---|---|---|---|---|---|
| `odoo_web` | `odoo-web.conf` | **6432** (pgbouncer) | `odoo_web` | 4 | 0 | yes | `127.0.0.1:8069` |
| `odoo_gevent` | `odoo-gevent.conf` | **5432** (direct) | `odoo_web` | 1 | 0 | yes (bus) | `127.0.0.1:8072` |
| `odoo_worker` | `odoo-worker.conf` | **5432** (direct) | `odoo_provision` | 0 | 1 | `--no-http` | **none** |

Config keys **common to all three** (verified):
```
db_host = <DB_HOST_IP>
db_sslmode = require
db_maxconn = 8
dbfilter = ^%d$
list_db = False
proxy_mode = True
session_cookie_domain = .msolutions-eg.com
saas_control_db = msolutions
server_wide_modules = base,web,saas_dbfilter
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/mnt/msolutions_custom
data_dir = /var/lib/odoo
limit_memory_hard = 4294967296      # 4 GiB
limit_memory_soft = 3221225472      # 3 GiB
limit_time_cpu = 600 ; limit_time_real = 1200 ; limit_request = 8192
```
`admin_passwd` (the Odoo master password, `<ADMIN_PASSWD>`) is set identically in
all three; it is only reachable via `/web/database/*`, which nginx 404s (§7).

**Why cron only on the worker:** cron is where provisioning runs (installing
`base` pins a CPU for ~1 min). It must run as `odoo_provision`, and it must not run
in a web worker (would block an HTTP thread). `max_cron_threads = 1` on the worker,
`0` everywhere else.

**Why `workers=0` on the worker:** `workers=0` gives a single-process server whose
cron thread runs; with `--no-http` it never serves a request. `odoo_web` uses
`workers=4` (prefork HTTP). `odoo_gevent` uses `workers=1` because the websocket
bus needs the gevent/evented server, not prefork.

---

## 4. POSTGRESQL ROLES AND THE OWNERSHIP SPLIT

Verified role attributes (`pg_roles`):

| role | SUPERUSER | CREATEDB | CREATEROLE | LOGIN |
|---|---|---|---|---|
| `odoo` | ✅ | ✅ | ✅ | ✅ | ← legacy superuser, owns `postgres` db |
| `odoo_provision` | ❌ | ✅ | ❌ | ✅ | ← creates & **owns tenant databases** |
| `odoo_web` | ❌ | ❌ | ❌ | ✅ | ← owns **schema objects**, daily DML/DDL |
| `pgbouncer_auth` | ❌ | ❌ | ❌ | ✅ | ← auth_query only (§6) |

**The split, and why it is counter-intuitive:** you normally give one role
everything. Here privilege is split by *object level*:

- **`DROP DATABASE` requires being the database owner.** The database is owned by
  `odoo_provision`, which only the non-published worker connects as. So the web
  tier physically cannot drop a tenant.
- **Daily work (DML + migrations/DDL) needs to own the tables**, not the database.
  So every schema object is owned by `odoo_web`.

One-way membership: **`GRANT odoo_web TO odoo_provision`** (verified:
`pg_auth_members` shows `odoo_provision → odoo_web`). `odoo_provision` can *act as*
`odoo_web` (needed to reassign object ownership to it after creating them); the
reverse grant does **not** exist, so `odoo_web` can never assume `odoo_provision`.

Verified live: database `sameh` and `msolutions` have `datdba = odoo_provision`;
all 134 tables in `sameh` are owned by `odoo_web`.

### Why `ALTER OWNER`, never `REASSIGN OWNED`
`REASSIGN OWNED BY odoo_provision TO odoo_web` looks like the one-liner that does
this. **It also changes the database owner to `odoo_web`** — which silently hands
the web tier `DROP DATABASE`, destroying the entire boundary. This was confirmed
empirically. The module therefore walks objects individually with `ALTER … OWNER`
and leaves the database owner alone. Exact SQL (from `saas_tenant.py`,
`_GRANT_WEB_OWNERSHIP_SQL`, run inside the new tenant DB as `odoo_provision`):

```sql
DO $r$
DECLARE x record;
BEGIN
  -- schemas owned by odoo_provision → odoo_web
  FOR x IN SELECT nspname FROM pg_namespace
           WHERE nspname NOT LIKE 'pg\_%' AND nspname<>'information_schema'
             AND pg_get_userbyid(nspowner)='odoo_provision'
  LOOP EXECUTE format('ALTER SCHEMA %I OWNER TO odoo_web', x.nspname); END LOOP;
  -- tables/partitions/sequences/views/matviews/foreign tables → odoo_web,
  -- skipping sequences OWNED BY a table column (deptype a/i): those follow
  -- their table and cannot be reassigned independently.
  FOR x IN SELECT n.nspname ns, c.relname rel, c.relkind kind
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname<>'information_schema'
             AND c.relkind IN ('r','p','S','v','m','f')
             AND pg_get_userbyid(c.relowner)='odoo_provision'
             AND NOT (c.relkind='S' AND EXISTS (SELECT 1 FROM pg_depend d
                      WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                        AND d.deptype IN ('a','i')))
  LOOP EXECUTE format('ALTER %s %I.%I OWNER TO odoo_web',
         CASE x.kind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                     WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                     ELSE 'TABLE' END, x.ns, x.rel);
  END LOOP;
  -- routines → odoo_web
  FOR x IN SELECT p.oid::regprocedure sig FROM pg_proc p
           JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname<>'information_schema'
             AND pg_get_userbyid(p.proowner)='odoo_provision'
  LOOP EXECUTE format('ALTER ROUTINE %s OWNER TO odoo_web', x.sig); END LOOP;
END $r$;
ALTER SCHEMA public OWNER TO odoo_web;
-- then, separately: GRANT CONNECT ON DATABASE <name> TO odoo_web;
```

`msolutions_saas` runs this automatically as `_grant_web_ownership()` at the end of
every provision, so a new tenant reproduces the split with zero manual steps.

**Verify the split on a new tenant** (from the DB host):
```bash
docker exec odoo_db psql -U odoo -d postgres -tAc \
  "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname='<tenant>';"
# expect: <tenant>|odoo_provision
docker exec odoo_db psql -U odoo -d <tenant> -tAc \
  "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='public';"
# expect: odoo_web
```
Bootstrapping the roles on a fresh cluster is in §10c.

---

## 5. NETWORK AND TRANSPORT SECURITY

Layers, from Postgres outward:

**a. TLS on Postgres** (`/opt/docker/postgres/postgresql.conf` on the DB host):
```
ssl = on
ssl_cert_file = 'server.crt'     # self-signed, CN=<DB_HOST_IP>, key 0600
ssl_key_file  = 'server.key'
listen_addresses = '*'
```

**b. `pg_hba.conf`** (DB host) — the internet-open `host all all all` rule was
**removed**. Effective rules:
```
local   all         all                              trust
host    all         all   127.0.0.1/32               trust
host    all         all   ::1/128                    trust
host    all         all   172.18.0.0/16              md5      # DB-host docker net (pgbouncer→db)
hostssl all         all   <ODOO_HOST_IP>/32          md5      # Odoo host, SSL required, /32
```
`hostssl` + `/32` means: only the Odoo host, only over TLS. A non-SSL connection
from that IP is refused (`no pg_hba.conf entry … no encryption`).

**c. Odoo requires SSL:** every `odoo-*.conf` sets `db_sslmode = require`.
Verified live: all Odoo↔PG sessions show `pg_stat_ssl.ssl = t`.

**d. Firewall** (`/usr/local/sbin/docker-user-fw.sh` on the DB host, run by
`docker-user-fw.service`, **enabled**). Because Docker's own rules bypass the
`INPUT` chain, the rule lives in **`DOCKER-USER`**:
```bash
IFACE=eth0 ; ODOO_IP=<ODOO_HOST_IP>
for P in 5432 6432; do
  iptables -I DOCKER-USER -i $IFACE -p tcp --dport $P ! -s $ODOO_IP \
           -m comment --comment saas-fw -j DROP
done
```
The systemd unit re-applies it on boot and after any `docker` restart (Docker
flushes `DOCKER-USER` on restart), so the rule survives. The script is idempotent
(strips its own `saas-fw`-commented rules first).

### ⚠️ ORDER MATTERS — apply in this sequence, or you lock Odoo out of its own DB
1. **Enable `ssl = on`** on Postgres and reload. (Certs in place first.)
2. **Set `db_sslmode = require`** in Odoo and restart Odoo. Confirm it connects
   over SSL (`pg_stat_ssl`).
3. **Only then tighten `pg_hba`** to `hostssl … /32` and reload.
4. **Only then add the firewall** drop rule.

Reversing this — tightening hba/firewall before Odoo is actually using SSL — cuts
Odoo off from Postgres. Postgres changes here are `SELECT pg_reload_conf()`, never
a restart (a restart drops every live connection).

---

## 6. PGBOUNCER

Runs on the DB host (`edoburu/pgbouncer`), defined in
`/opt/docker/docker-compose.yml`, env-driven. Publishes `0.0.0.0:6432` (firewalled
to the Odoo host), connects to `db:5432` on the internal `backend` network.

**Who uses it, and the counter-intuitive part:**
- `odoo_web` → **pgbouncer :6432**, `POOL_MODE=transaction`. The HTTP tier opens
  and releases many short transactions; transaction pooling multiplexes them onto
  a few server connections.
- `odoo_gevent` → **Postgres :5432 directly, bypassing pgbouncer.** The websocket
  bus uses **`LISTEN`/`NOTIFY`**, which needs a *persistent session*. Transaction
  pooling returns the server connection to the pool after each transaction and the
  `LISTEN` registration is silently lost — the bus goes quiet with no error. So the
  bus tier must connect direct. This looks like an inconsistency to "clean up"; it
  is load-bearing.
- `odoo_worker` → **:5432 directly** too (as `odoo_provision`); provisioning does
  DDL/`CREATE DATABASE` outside normal transaction scope.

**auth_query, so new tenants/roles need zero pgbouncer changes:**
```
AUTH_TYPE   = scram-sha-256
AUTH_USER   = pgbouncer_auth
AUTH_QUERY  = SELECT username, password FROM public.pgbouncer_get_auth($1)
AUTH_DBNAME = postgres
```
pgbouncer authenticates every client by calling `pgbouncer_get_auth` **in the
`postgres` database** (so it works before the target tenant DB is even known). The
function is `SECURITY DEFINER` (created/owned by superuser) so the minimal
`pgbouncer_auth` role can read `pg_authid`:
```sql
CREATE OR REPLACE FUNCTION public.pgbouncer_get_auth(p_username text)
  RETURNS TABLE(username text, password text)
  LANGUAGE sql SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $$ SELECT rolname::text, rolpassword::text FROM pg_authid
      WHERE rolname = p_username AND rolcanlogin; $$;
-- GRANT EXECUTE ON FUNCTION public.pgbouncer_get_auth(text) TO pgbouncer_auth;
```
A brand-new tenant database and any new role authenticate immediately — nothing in
pgbouncer is per-tenant.

**Client TLS:** `CLIENT_TLS_SSLMODE=require` with
`CLIENT_TLS_CERT_FILE=/etc/pgbouncer/certs/server.crt` (+ key). So even Odoo→pgbouncer
is encrypted, not just Odoo→Postgres.

**Sizing — verified, and why the defaults are dangerous here:**
```
POOL_MODE=transaction   MAX_CLIENT_CONN=2000
DEFAULT_POOL_SIZE=5     MIN_POOL_SIZE=0     RESERVE_POOL_SIZE=2  RESERVE_POOL_TIMEOUT=3
MAX_DB_CONNECTIONS=10   MAX_USER_CONNECTIONS=150
SERVER_IDLE_TIMEOUT=60  MAX_PREPARED_STATEMENTS=200
IGNORE_STARTUP_PARAMETERS=extra_float_digits
```
pgbouncer allocates a pool **per (database, user) pair**. This is a
**many-databases, low-concurrency-each** workload: 300 tenants × a naive
`DEFAULT_POOL_SIZE=20` would be 6000 server connections against Postgres's
`max_connections = 100`. So the pool is deliberately tiny (`5`), `MIN_POOL_SIZE=0`
(idle tenants hold nothing), `MAX_DB_CONNECTIONS=10` caps any one tenant, and
`SERVER_IDLE_TIMEOUT=60` reaps idle server conns fast. The connection budget is the
scarce resource; treat any pool-size increase as multiplied by tenant count.

---

## 7. NGINX AND ROUTING

One file, `/opt/odoo/nginx.conf`, bind-mounted read-only into `odoo_nginx`. Plus
`/opt/odoo/conf.d/tenants.conf` (the tenant allow-map, mounted at
`/etc/nginx/conf.d/`).

**Wildcard subdomain routing + explicit allow-map.** A `map` gates which hosts are
valid tenants:
```nginx
map $host $valid_tenant {
    default 0;
    include /etc/nginx/conf.d/tenants.conf;   # "<tenant>.msolutions-eg.com 1;" lines
}
```
Two HTTPS server blocks share the wildcard cert
(`/etc/letsencrypt/live/msolutions-eg.com/…`):
- `server_name msolutions-eg.com www.msolutions-eg.com` — the **main/control** domain.
- `server_name ~^(?<dbname>.+)\.msolutions-eg\.com$` — **tenant subdomains**;
  returns 404 when `$valid_tenant = 0`.

**dbfilter picks the database from the host.** Odoo config `dbfilter = ^%d$` maps
the first hostname label to the database name; `list_db = False` disables the DB
selector entirely. So `sameh.msolutions-eg.com` → database `sameh`.

**The main domain needs a Host rewrite.** The control plane is database
`msolutions`, but the apex host's first label is `msolutions-eg`, which is not a
database. So the main-domain block rewrites the upstream Host header:
```nginx
proxy_set_header Host msolutions.msolutions-eg.com;   # → dbfilter %d = "msolutions"
```
Tenant blocks pass `Host $host` unchanged (the label already equals the database).

**Two upstreams — the websocket split:**
```nginx
upstream odoo_servers   { server odoo_web:8069;    keepalive 64; }
upstream odoo_websocket { server odoo_gevent:8072; keepalive 64; }
# location /websocket and /longpolling/poll → odoo_websocket (Upgrade/Connection headers)
# location / → odoo_servers
```

**Database-management lockdown** (defense in depth; `list_db` is already False).
Present in *both* HTTPS server blocks, before `location /`:
```nginx
location ~ ^/web/database/(manager|create|duplicate|drop|restore|backup|change_password) { return 404; }
```
Verified live: `/web/database/manager` → **404** on both the main domain and a
tenant subdomain.

### The tenant allow-map is generated, and NOT wired to the module ⚠️
`/opt/odoo/conf.d/tenants.conf` is produced by `/opt/scripts/update_tenant_list.sh`,
which **enumerates tenants dynamically** (`SELECT datname FROM pg_database WHERE
datname NOT IN ('postgres','template0','template1','msolutions') AND NOT
datistemplate`), writes one `1;` line per tenant + the apex, and runs
`nginx -s reload`.

⚠️ **GAP:** `msolutions_saas` does **not** call this script on provision, and it is
on no cron/timer. So after the module creates a tenant, its subdomain returns 404
until someone runs `update_tenant_list.sh`. Run it after every provision/drop:
```bash
/opt/scripts/update_tenant_list.sh
```
⚠️ **SECURITY:** this legacy script currently embeds the Postgres **superuser**
password in plaintext (`DB_PASS=…`). It should be rewritten to source a 600 env
file and use a least-privilege role, or folded into the module. See §15.

### ⚠️ The single-file bind-mount inode trap — READ THIS
`nginx.conf` (and each `odoo-*.conf`) is bind-mounted as a **single file**. A bind
mount is pinned to the file's **inode**. `cp newfile nginx.conf`, `sed -i`, and most
editors **replace the inode** (write-new-then-rename), so the *container keeps
seeing the OLD file* — and `nginx -s reload` then reloads stale config with no
error. This has bitten this system. Correct procedure:
```bash
# edit in place — preserves the inode
cat /path/to/edited-nginx.conf > /opt/odoo/nginx.conf
# verify the container sees the same inode as the host
stat -c '%i' /opt/odoo/nginx.conf
docker exec odoo_nginx stat -c '%i' /etc/nginx/nginx.conf   # must match
docker exec odoo_nginx nginx -t && docker exec odoo_nginx nginx -s reload
```

---

## 8. THE `saas_dbfilter` MODULE

`/opt/odoo/msolutions_custom/saas_dbfilter/` — a tiny server-wide module, loaded via
`server_wide_modules = base,web,saas_dbfilter`.

**Why it exists.** Odoo core `list_dbs()` enumerates databases the connecting role
**owns** (`datdba = current_user`). Once we moved database ownership to
`odoo_provision` (§4), the web tier (`odoo_web`) owned **no** databases, so
`dbfilter` matched nothing and every subdomain fell through to the DB selector (a
broken login page). This patch swaps the *owner* filter for a **CONNECT-privilege**
filter, which `odoo_web` does have on every tenant:

```python
# __init__.py — patched at import (runs in every process at startup)
import odoo, odoo.service.db as _dbsvc
def _list_dbs_by_connect(force=False):
    ...
    cr.execute("select datname from pg_database "
               "where has_database_privilege(current_user, datname, 'CONNECT') "
               "and not datistemplate and datallowconn and datname not in %s "
               "order by datname", (templates_list,))
    ...
_dbsvc.list_dbs = _list_dbs_by_connect
```
It matches core behaviour in every other respect (respects `list_db`/`db_name`,
excludes templates). Because it's on `server_wide_modules` the monkey-patch is
applied before any request is served, in web, gevent and worker alike.

---

## 9. BACKUPS — the most important section

Driver: `/opt/scripts/backup_tenants.sh` (bash, runs as **root** on the Odoo host).
Config: `/opt/scripts/backup.env` (root, `600`). Scheduler:
`odoo-backup.timer` → `odoo-backup.service`, `OnCalendar=*-*-* 02:30:00 UTC`,
`Persistent=true`.

**Per tenant, each run** (tenants enumerated dynamically by CONNECT privilege, same
predicate as §8):
1. `pg_dump -Fp` → `/opt/backups/local/<date>/<tenant>.sql` (plain SQL, over SSL,
   as `odoo_web`). A dump < 1000 bytes is treated as failure.
2. `tar czf` the tenant's filestore → `<tenant>.filestore.tar.gz`.
3. Local retention: date dirs older than `LOCAL_KEEP_DAYS=7` are removed; likewise
   `pre_drop_backups` files older than 7 days.
4. **restic → Backblaze B2** of the day's dir **plus** `/opt/odoo/filestore/pre_drop_backups`.

On success writes `/opt/backups/LAST_BACKUP_OK` (timestamp); on any failure writes
`/opt/backups/LAST_BACKUP_FAILED`, exits non-zero, and alerts (§14/§15).

### Two restic tiers
- **`repo_daily`** (`RESTIC_REPOSITORY`): the live offsite repo. After each backup:
  `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3` (marks snapshots
  unreferenced; does **not** free space).
- **`repo_locked`** (`RESTIC_REPOSITORY_LOCKED`): append-only, `restic backup
  --no-lock`, **never pruned** — designed for B2 Object Lock. ⚠️ **Staged, not
  active** (§15): the block runs only when `LOCKED_AWS_ACCESS_KEY_ID` +
  `RESTIC_PASSWORD_LOCKED` are set, and they are blank.

### forget vs prune, and why both
`forget` (nightly, in `backup_tenants.sh`) only updates the retention policy —
cheap, safe to run often. **`prune`** (weekly, separate script
`/opt/scripts/restic_prune.sh`, `odoo-prune.timer` Sun `03:30 UTC`) is what actually
repacks and reclaims space — expensive and I/O-heavy, so it is *not* run nightly.
Splitting them keeps the nightly job fast and lets a prune failure alert
independently (`LAST_PRUNE_FAILED`).

### Why `-Fp` (plain) and not `-Fc` (custom/compressed)
restic deduplicates by content-defined chunking across snapshots. A compressed
`-Fc` dump changes almost every byte day to day, defeating dedup — the repo grows
by a full copy each night. Plain `-Fp` dumps share most chunks, so an incremental
night adds little (verified: first snapshot 15.6 MiB stored; a re-run added ~1.0
MiB). restic compresses at rest anyway.

### Why `pg_dump` runs as `odoo_web` (the *less* privileged role) — counter-intuitive
`odoo_web` can only read objects it owns. If ownership ever drifts (a table ends up
owned by `odoo_provision`), `pg_dump` as `odoo_web` **fails loudly**
(`permission denied for table …`, exit 1) and the job refuses the backup.
`odoo_provision` (or `odoo`) would dump *successfully* while quietly omitting or
misreading the drifted objects. **A backup that succeeds while incomplete is more
dangerous than one that fails** — you find out at restore time, in the worst
moment. Verified: dump as `odoo_web` over an unreadable table exits 1 and the
partial file is discarded; table-count parity dump-vs-`information_schema` matched.

### Backup-before-drop
`msolutions_saas._drop_database()` calls `_backup_before_drop()` **first**: a direct
`pg_dump -Fp --no-owner` + filestore tar into `data_dir/pre_drop_backups`, on the
Odoo host (a *different machine* than Postgres). If the dump fails, or is
implausibly small (< 4096 bytes), it **raises** and the drop is aborted
(`Refusing to drop … No data was deleted.`). Proven both ways: refused on a failed
backup; succeeded after a valid one. The nightly job sweeps `pre_drop_backups` to
B2, so pre-drop safety copies are also offsite.

### Freshness indicator (MOTD)
`/opt/scripts/backup_status.sh` (called by `/etc/update-motd.d/99-odoo-backup`)
prints on every SSH login: **green `[BACKUP OK]` when the last offsite success is
< 48 h old, red `[BACKUP STALE]` at ≥ 48 h or never.** Verified output:
`[BACKUP OK] offsite (B2) last success 3h ago`.

### ⚠️ Object Lock is enabled but INERT
The B2 bucket has `ObjectLockEnabled=Enabled`, **but no default retention rule is
set**, so restic objects show `ObjectLockMode=None` / `RetainUntilDate=None` —
**nothing is actually immutable.** Do not describe the current backups as
ransomware-proof. Making it real is the `repo_locked` work in §15 (governance-mode
retention conflicts with `prune`, hence the separate never-pruned repo).

---

## 10. RESTORE PROCEDURES — written for 3 a.m.

All restic commands need the repo env. Load it once per shell:
```bash
set -a ; . /opt/scripts/backup.env ; set +a
export RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
restic snapshots            # success = a table of snapshots prints, no error
```
`db_dump` via the UI is disabled (`list_db=False`); restores are done with
`psql`/`pg_restore` + `tar`, not the Odoo web UI.

### a. Restore ONE tenant from B2
```bash
# 1. Load env (above). Pick the snapshot:
restic snapshots                                  # note the ID with the date you want
# 2. Restore that snapshot's files to a scratch dir:
restic restore <SNAPSHOT_ID> --target /opt/restore
#    success = files appear under /opt/restore/opt/backups/local/<date>/
ls /opt/restore/opt/backups/local/*/<tenant>.sql  # must exist and be > few KB
# 3. Create an EMPTY database owned by odoo_provision (run on DB host, as odoo):
docker exec odoo_db psql -U odoo -d postgres -c \
  "CREATE DATABASE <tenant> OWNER odoo_provision;"
#    (restoring over a LIVE tenant? drop it via the control plane first, or restore
#     into a scratch name like <tenant>_restore and rename after verifying.)
# 4. Load the SQL (from the Odoo host, over SSL, as odoo_web-or-superuser):
PGSSLMODE=require psql "host=<DB_HOST_IP> port=5432 user=odoo dbname=<tenant>" \
  -f /opt/restore/opt/backups/local/<date>/<tenant>.sql
#    success = ends with no ERROR; a few "already exists" NOTICEs are fine.
# 5. Restore the filestore:
tar xzf /opt/restore/opt/backups/local/<date>/<tenant>.filestore.tar.gz \
    -C /opt/odoo/filestore/filestore/
# 6. Re-apply the ownership split (objects → odoo_web) — see §4 SQL block, or:
#    run the tenant through the module's _grant_web_ownership equivalent.
# 7. Add the subdomain and reload nginx:
/opt/scripts/update_tenant_list.sh
# 8. Verify: open https://<tenant>.msolutions-eg.com/web/login → HTTP 200,
#    log in, spot-check row counts against what you expect.
```

### b. Restore ALL tenants after total loss of the Odoo host
The Odoo host is **stateless except** `/opt/odoo/config/*`, `/opt/odoo/nginx.conf`,
`/opt/odoo/docker-compose.yml`, the code (`/opt/odoo/msolutions_custom`, from git),
and the filestore (which is in restic). Postgres data lives on the *other* host and
is unaffected by losing the Odoo host.
```
1. Provision a new VPS, install Docker + docker-compose + restic (§10c steps 1–2).
2. git clone the repo to /opt/odoo/msolutions_custom.
3. Recreate /opt/odoo/{docker-compose.yml,nginx.conf,config/*.conf,conf.d} — from
   git if committed, else from this document (§3, §7). Put the wildcard cert back
   under /etc/letsencrypt (certbot DNS challenge) OR restore /etc/letsencrypt.
4. Recreate /opt/scripts/backup.env with the SAME RESTIC_PASSWORD and B2 keys (§14)
   — without the original RESTIC_PASSWORD the repo is unreadable (§14).
5. Point db_host at the surviving DB host; the databases are still there.
6. restic restore latest --target /opt/restore ; extract each
   <tenant>.filestore.tar.gz into /opt/odoo/filestore/filestore/.
7. docker-compose up -d ; /opt/scripts/update_tenant_list.sh
8. Verify each subdomain returns 200 and the MOTD shows [BACKUP OK] after the next run.
```
If the databases were lost too, restore each from B2 per §10a.

### c. Rebuild the WHOLE stack on a fresh VPS from scratch
Assumes two fresh hosts (or one host, two compose stacks). Everything below is
literal; substitute placeholders.

```
# ---------- DB HOST ----------
1. apt-get update && apt-get install -y docker.io docker-compose
2. mkdir -p /opt/docker/postgres /opt/docker/pgbouncer
3. Generate the Postgres server cert (self-signed):
   openssl req -new -x509 -days 3650 -nodes -subj "/CN=<DB_HOST_IP>" \
     -keyout /opt/docker/postgres/server.key -out /opt/docker/postgres/server.crt
   chmod 600 /opt/docker/postgres/server.key   # postgres refuses a group/world-readable key
   # reuse the same pair (or a copy) for /opt/docker/pgbouncer/server.{crt,key}
4. Write /opt/docker/postgres/postgresql.conf with: ssl=on, ssl_cert_file/… (§5a),
   listen_addresses='*', max_connections=100.
5. Write /opt/docker/postgres/pg_hba.conf with EXACTLY the rules in §5b
   (hostssl <ODOO_HOST_IP>/32 md5 ; 172.18.0.0/16 md5 ; localhost trust).
6. Write /opt/docker/.env (PGBOUNCER_AUTH_PW=…, MINIO_ROOT_* if used) and
   /opt/docker/docker-compose.yml with the postgres + pgbouncer services (§6 has
   the pgbouncer env verbatim). Start postgres only first:
   docker-compose -f /opt/docker/docker-compose.yml up -d db
7. Create roles (as the postgres superuser):
   CREATE ROLE odoo LOGIN SUPERUSER PASSWORD '<PG_SUPERUSER_PW>';          -- if not present
   CREATE ROLE odoo_provision LOGIN NOSUPERUSER CREATEDB NOCREATEROLE PASSWORD '…';
   CREATE ROLE odoo_web       LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '…';
   CREATE ROLE pgbouncer_auth LOGIN PASSWORD '<PGBOUNCER_AUTH_PW>';
   GRANT odoo_web TO odoo_provision;                                        -- one-way (§4)
8. Create pgbouncer_get_auth in the postgres DB (§6 SQL) and
   GRANT EXECUTE … TO pgbouncer_auth.
9. Start pgbouncer:  docker-compose -f /opt/docker/docker-compose.yml up -d pgbouncer
10. Firewall: install /usr/local/sbin/docker-user-fw.sh (§5d) +
    /etc/systemd/system/docker-user-fw.service ; systemctl enable --now docker-user-fw
    # Apply §5 in ORDER: ssl already on (step 4) → Odoo will use SSL → hba (step 5) → firewall.

# ---------- ODOO HOST ----------
11. apt-get install -y docker.io docker-compose restic
12. git clone https://github.com/SamehAbdelAlYoussef/msolutions_custom.git \
        /opt/odoo/msolutions_custom
13. Create /opt/odoo/{config,conf.d,filestore,logs,html,ssl,redis} ; put the wildcard
    cert under /etc/letsencrypt (certbot --dns... -d '*.msolutions-eg.com' -d msolutions-eg.com).
14. Write /opt/odoo/config/odoo-{web,gevent,worker}.conf (§3 table + common keys;
    db_host=<DB_HOST_IP>, matching db_user/db_port per file, a fresh admin_passwd).
15. Write /opt/odoo/nginx.conf (§7) and /opt/odoo/docker-compose.yml (§3).
16. docker-compose -f /opt/odoo/docker-compose.yml up -d
17. Create the control-plane DB and install the module (worker connects as
    odoo_provision, which can CREATEDB):
    docker exec odoo_worker odoo -c /etc/odoo/odoo.conf -d msolutions \
       -i msolutions_saas --stop-after-init --no-http
    # then run _grant_web_ownership equivalent on 'msolutions' (§4 SQL) so odoo_web owns its objects.
18. /opt/scripts/update_tenant_list.sh   (adds apex to nginx map)
19. Set up backups: write /opt/scripts/backup.env (§14), install
    /opt/scripts/backup_tenants.sh + restic_prune.sh + backup_status.sh +
    /etc/update-motd.d/99-odoo-backup, and the odoo-backup/odoo-prune timers.
    restic init the B2 repo (first time only):
      set -a; . /opt/scripts/backup.env; set +a
      restic init         # success = "created restic repository … "
20. Verify: https://msolutions-eg.com/web/login → 200 ; provision a test tenant via
    the control plane ; confirm §4 ownership ; run a backup + a restore (§10a) ; drop the test.
```

### d. What to check first when `[BACKUP STALE]` appears
```
1. cat /opt/backups/LAST_BACKUP_FAILED        # the exact failing step + time
2. ls -t /opt/backups/logs | head ; less the newest backup-*.log
3. Common causes, in order:
   - B2 creds/keys rotated or revoked → restic snapshots errors "AccessDenied"
     → fix AWS_ACCESS_KEY_ID/SECRET in backup.env (mapped from the B2 app key, §14).
   - Staging disk full → pg_dump "No space left" → df -h /opt ; clear old date dirs.
   - DB host unreachable / hba or firewall changed → psql "host=<DB_HOST_IP>…"
     from the Odoo host; check the DB-host firewall didn't drop the Odoo IP.
   - pg_dump "permission denied for table" → OWNERSHIP DRIFT (§4/§9). Do NOT bypass
     by dumping as a superuser — fix ownership (re-run the §4 grant), that error is
     the safety net working.
4. Fix, then force a run:  systemctl start odoo-backup.service ; watch the log ;
   confirm LAST_BACKUP_OK updates and MOTD goes green.
```

---

## 11. THE `msolutions_saas` MODULE

`/opt/odoo/msolutions_custom/msolutions_saas/`. Installs in the **control-plane
database only**. Models: `saas.tenant`, `saas.audit.log`, `saas.config`.

**State machine (`saas.tenant.state`):**
```
draft ──action_provision──▶ provisioning ──▶ active
  ▲                             │  │            │
  │        (worker: _run_provision)  └─fail─▶ error ──action_retry──▶ provisioning
  │                                            │  (only if NOT ever_active)
active/error ──action_drop──▶ terminating ──▶ terminated
                                 └────fail───▶ error
```
- User actions (`action_provision/drop/retry`) run in the **web tier**: they only
  validate, flip state, write `state_since`, and trigger the cron. They never touch
  a database directly (the web tier is `odoo_web`, which cannot create/drop).
- The **worker cron** (`_cron_process`) does the real work as `odoo_provision`,
  **one tenant per run** (installing `base` pins a CPU ~1 min).
  `_claim_one()` uses `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` so a second cron
  thread never double-claims the same record.
- **Provision flow** (`_run_provision`): if a database already exists it's a
  half-finished retry remnant → `_drop_database_raw()` first; then
  `_provision_database` (`odoo_db._create_empty_database`) → `_provision_odoo`
  (`_initialize_db` installs `base`, sets a random 16-char admin password) →
  `_grant_web_ownership` (§4) → state `active`, `ever_active=True`. The record is
  **committed even on failure** (the database exists outside the ORM transaction;
  a rollback would orphan it).
- **Drop flow** (`_run_drop`/`_drop_database`): `_backup_before_drop()` (§9) →
  `_drop_database_raw()` (tear down registry, `close_db`, `DROP DATABASE`, remove
  filestore).
- **Watchdog** (`_cron_watchdog`, separate cron): tenants stuck in
  `provisioning`/`terminating` longer than
  `msolutions_saas.stuck_timeout_minutes` (default 15) → `error`, so a worker that
  died mid-run doesn't leave a record stuck forever. Commits per tenant.
- **Retry guard:** `action_retry` refuses if `ever_active` (a once-live tenant
  holds real data; re-provisioning would drop it). Use Drop instead.

**The control-plane guard — why `odoo.conf`, not `ir_config_parameter`.**
`_assert_control_plane()` reads `saas_control_db` from **`odoo.conf`**
(`odoo.tools.config.get`). The module is on the shared addons path, so it appears
in every tenant's Apps list; installed in a tenant it must not become a working
provisioning console. `odoo.conf` is mounted **read-only** and is the **same file
for every database**, so a tenant cannot edit a parameter to nominate *itself* as
the control plane. An `ir.config_parameter` lives *inside each tenant's DB* and a
tenant admin could set it. When `saas_control_db` is unset the guard stays open (so
local/dev setups work unchanged).

**Reserved names & validation.** `NAME_RE = ^[a-z][a-z0-9]{2,30}$` (3–31 chars,
leading letter — satisfies both DB-identifier and DNS-label rules). `RESERVED_NAMES`
blocks `postgres/template0/template1/odoo/root/www/mail/api/app/admin/saas/
msolutions/…` — collisions the old `create_tenant.sh` never checked.

**Security — three layers** (defence in depth):
1. `security/ir.model.access.csv` — ORM access only for `group_saas_developer`.
2. `security/saas_security.xml` `ir.rule` — record rules `[(1,'=',1)]` for the same
   group; still applies when a third-party module uses `sudo()`.
3. `_check_developer_group()` — explicit code check at the entry of every
   RPC-reachable `@api.model` method, so a direct JSON-RPC call can't skip 1–2.
`base.group_system` implies `group_saas_developer`, so admins get it automatically.

**Audit log (`saas.audit.log`).** One immutable row per action (provision/drop/
retry/shell query): who (`user_id`, captured *before* `sudo()` so cron shows the
real user), what, which tenant (`tenant_name` snapshotted so it survives tenant
deletion), detail. `write()` and `unlink()` are overridden to **raise** — immutable
even to `sudo()`. CSV also sets `perm_write=0/perm_unlink=0`.

**Odoo-19 specifics.** Uniqueness uses **`models.Constraint("UNIQUE (name)", …)`**,
not the removed `_sql_constraints` (Odoo 19 ignores the latter with a warning). The
module `depends` on `html_editor` only to satisfy a core SCSS variable the
otherwise-tiny control DB would miss (without it both asset bundles fail to compile
and the backend renders unstyled) — documented in the manifest. It is deliberately
**not** an `application` (as the only app it would hijack `/odoo`'s landing action).

**The seam for moving off Contabo:** the provisioning steps are separate methods;
the one that changes if Postgres becomes managed is `_provision_database` — override
it in a module inheriting `saas.tenant`.

---

## 12. HARD RULES FOR ANY AGENT WORKING HERE

1. **Never open a tenant Registry or use the ORM against a tenant DB.** Raw psycopg
   via `odoo.sql_db.db_connect(<db>)` inside `closing()`, released immediately. A
   registry load is 150–300 MB of worker RAM — the binding limit (§16). The module
   follows this everywhere (`shell_execute`, `get_db_activity`, `_database_exists`).
2. **Everything works identically at 3 and at 300 tenants. Enumerate dynamically**
   (`has_database_privilege(current_user, datname, 'CONNECT')`), never a hardcoded
   list. (The nginx map is generated from exactly this query — §7.)
3. **B2/restic creds must never be reachable from an Odoo container.** They live
   only in `/opt/scripts/backup.env` (root 600), not in any Odoo config or DB.
   Object Lock (when active) stops deletion, not reading — a leaked *read* key
   still exfiltrates every backup.
4. **The server pulls; it does not push.** Code flows local → GitHub → CI → box.
   No push credential on the box (§13).
5. **Community only.** No Enterprise, no OEEL-licensed, no license-bypass modules.
   Check every manifest `license`. (History note: Enterprise addons and their
   databases were removed for exactly this reason.)
6. **Never make a tenant read-only or suspend it over a quota.** Soft limits only.
7. **Postgres config changes are `pg_reload_conf()`, never a restart** (a restart
   drops every tenant's live connections).
8. **Respect the §5 ordering** whenever touching SSL/hba/firewall, or you lock Odoo
   out of its own database.

---

## 13. DEPLOYMENT

**Repo:** `github.com/SamehAbdelAlYoussef/msolutions_custom`, branch `main`,
deployed at `/opt/odoo/msolutions_custom` (remote is clean HTTPS, no token).

**CI:** `.github/workflows/deploy.yml`. It authenticates with an SSH key stored as
a **GitHub Actions secret** (`SSH_PRIVATE_KEY`/`SSH_HOST`/`SSH_USER`) via
`appleboy/ssh-action`, then runs on the box:
```
cd /opt/odoo/msolutions_custom && git pull origin main
cd /opt/odoo && docker-compose restart odoo_web odoo_gevent odoo_worker
```
The box-side `git pull` is anonymous over the public repo — it does not use the
(revoked) token; removing the token does not affect CI.

> ⚠️ **Reality vs. intent.** The committed workflow triggers on **`on: push`**
> (branches: main), **not** `workflow_dispatch`. `/usr/bin/docker-compose` exists,
> so the restart step works as written.
> ⚠️ **UNVERIFIED:** the workflow was disabled on GitHub's side earlier in
> hardening; its current enabled/disabled state cannot be checked from the box
> (no GitHub token). Confirm in the GitHub Actions UI before relying on auto-deploy.

### THE PUSH GAP — the thing that will bite an emergency
The server can `git pull` but **cannot `git push`** (no token, no deploy key — by
design: the box holds prod DB creds and B2 access, so a push credential here means a
box compromise can rewrite deployed code). Verified: `git push` → exit 128
(`could not read Username`); `git pull --ff-only` → works.

**Consequence:** a hotfix edited live on prod exists *only* on the box. The next CI
`git pull` **silently overwrites it** with the repo version — the outage returns and
no bad commit was ever pushed. To carry a live fix back:
```
# on the box:
cd /opt/odoo/msolutions_custom
git diff > /root/hotfix-$(date -u +%Y%m%d-%H%M).patch
# on your laptop:
scp root@<ODOO_HOST_IP>:/root/hotfix-*.patch .
git checkout main && git pull && git apply hotfix-*.patch
git add -A && git commit -m "hotfix: <what broke>" && git push origin main   # CI redeploys the SAME fix
# back on the box, make future pulls clean:
git stash && git pull --ff-only origin main && git stash drop
```
Never re-add a push token to the server to shortcut this.

---

## 14. SECRETS INVENTORY

Names and locations only — **no values anywhere in this repo.**

| Secret | Lives on disk at | What breaks without it | How to obtain / rotate |
|---|---|---|---|
| PG superuser (`odoo`) password | DB-host psql role; used by legacy `/opt/scripts/*_tenant.sh` ⚠️ (§15) | superuser DDL / legacy scripts | `ALTER ROLE odoo PASSWORD …` on the DB host |
| `odoo_provision` password | Odoo host `config/odoo-worker.conf` (`db_password`/`admin_passwd` area) | provisioning + drops fail | `ALTER ROLE odoo_provision PASSWORD …`, update worker.conf |
| `odoo_web` password | `config/odoo-web.conf` & `odoo-gevent.conf` | all HTTP + bus down | `ALTER ROLE odoo_web PASSWORD …`, update both confs |
| `PGBOUNCER_AUTH_PW` | DB host `/opt/docker/.env` | pgbouncer can't run auth_query → `odoo_web` tier can't log in | `ALTER ROLE pgbouncer_auth PASSWORD …`, update `.env`, recreate pgbouncer |
| Odoo master pw (`admin_passwd`) | all three `config/odoo-*.conf` | only gates `/web/database/*` (already 404'd) | generate 36-char random; set identically in all three |
| `RESTIC_PASSWORD` | Odoo host `/opt/scripts/backup.env` (600) | **repo permanently unreadable — see below** | generated on-server once; **store offline, cannot be regenerated** |
| `RESTIC_PASSWORD_LOCKED` | `backup.env` (blank; staged) | `repo_locked` unreadable | same as above, for the locked repo |
| B2 app key → `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `backup.env` | offsite backup + restore fail | Backblaze console → App Keys; the S3 backend uses the B2 keyID/appKey **as** AWS_ACCESS_KEY_ID/SECRET (the native `B2_ACCOUNT_ID/KEY` are for the `b2:` backend, not `s3:`) |
| `LOCKED_AWS_ACCESS_KEY_ID/SECRET` | `backup.env` (blank; staged) | `repo_locked` tier off | Backblaze App Key scoped to the locked bucket |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | `backup.env` (blank) | backup-failure alerts don't send | BotFather token + chat id from `getUpdates` |
| `ALERT_WEBHOOK` | `backup.env` | generic webhook alert off | any JSON webhook URL |
| SSH key for CI | **GitHub Actions secret** `SSH_PRIVATE_KEY` (not on the box's git) | auto-deploy stops (manual pull still works) | generate a keypair; public half in the box's `authorized_keys`, private half in the GitHub secret |
| DB-host root access | Odoo host `/root/.dbpass` (used by `sshpass`) | can't reach DB host from Odoo host | server root password |

### ⚠️ `RESTIC_PASSWORD` is unrecoverable
restic encrypts the repo with it. **If it is lost, every backup in B2 is
permanently unreadable — Backblaze cannot help, there is no reset.** Keep an offline
copy (password manager / sealed envelope) independent of both servers. Losing it is
equivalent to losing every backup.

`.gitignore` must cover: `backup.env`, `secrets.env`, `*.bak-*`, `__pycache__`,
`*.pyc`. Never commit an IP or any of the above.

---

## 15. KNOWN GAPS AND OPEN ITEMS

- **Object Lock is INERT (§9).** Bucket has Object Lock *enabled* but **no default
  retention rule**, so no object is actually immutable. *Blocks:* ransomware/insider
  protection. The nightly backups are deletable by anyone with the write key.
- **`repo_locked` not built.** The append-only `--no-lock` tier is staged in the
  script but its keys/password are blank. *Blocks:* the immutable offsite copy.
  Note governance-mode retention conflicts with `restic prune`, which is why it's a
  separate never-pruned repo.
- **nginx tenant map not wired to the module (§7).** After the module provisions a
  tenant, its subdomain 404s until `update_tenant_list.sh` is run manually. *Blocks:*
  hands-off provisioning. Fix: call it from `_run_provision`/`_run_drop`, or cron it.
- **Legacy shell scripts embed the PG superuser password in plaintext (§7).**
  `/opt/scripts/{create,delete,update_tenant_list}_tenant.sh` predate the module and
  hardcode `DB_PASS`. *Blocks:* clean secret hygiene. Fix: source a 600 env file +
  least-privilege role, or delete them (the module supersedes create/delete).
- **Stale-provisioning watchdog** — a `_cron_watchdog` exists in code (§11); confirm
  it's the deployed version and that its cron (`cron_watchdog`) is active. The
  original bug (a tenant stuck in `provisioning` >2 h) is addressed by it.
- **Telegram alerts not wired.** `alert()` supports Telegram but
  `TELEGRAM_BOT_TOKEN`/`CHAT_ID` are blank. *Blocks:* push notification of backup
  failure (today it's the 48 h MOTD + markers only).
- **Postgres on a separate host with NO private link (#1 scaling item).** Odoo
  reaches Postgres public→public over TLS. *Blocks:* latency-sensitive scale and a
  clean trust boundary. Every query pays WAN latency; the only thing between the
  world and 5432 is a `/32` hba rule + a firewall. A private network (or moving
  Postgres onto the Odoo host / a managed PG with private peering) is the highest-
  value infra change.
- **No sharding routing table (§16).** All tenants live in one cluster; there's no
  map of tenant→shard, so adding a second DB host is a manual migration, not a row.
- **No monitoring beyond the MOTD.** Backup freshness shows on SSH login only; there
  is no metrics/alerting stack (Prometheus, uptime checks, PG dashboards). *Blocks:*
  noticing anything before a human logs in.
- **redis running but unwired (§2).** Present, not referenced by Odoo config.
- **`workflow_dispatch` vs `on: push` (§13)** and the workflow's enabled state on
  GitHub — reconcile intent with the committed file.

---

## 16. CAPACITY AND SCALING

**The binding constraint is RAM, not connections.** Every *live* tenant database is
an Odoo **registry** in worker memory (150–300 MB each). Connections are cheap and
already pooled (§6); registries are not. This is why the hard rule "never open a
tenant Registry" (§12.1) exists — a careless ORM call against a tenant loads a
300 MB registry into a web worker.

**Current ceiling: roughly 30–60 tenants per Odoo instance**, set by
`workers=4 × limit_memory_hard=4 GiB` against host RAM and how many registries stay
warm. It degrades gradually (registry churn / eviction), not with a hard wall, so
watch worker RSS and swap, not a tenant count.

**Why horizontal sharding beats vertical scaling here.** Adding RAM to one box
helps linearly but concentrates blast radius and still shares one Postgres and one
worker pool. Because tenants are already fully isolated (one DB, one filestore dir,
one subdomain), they shard **cleanly**: put tenants A–M on instance 1, N–Z on
instance 2, each with its own Odoo cluster and (ideally) its own PG. Nothing in a
tenant assumes a neighbour exists.

**The routing-table-first approach.** The single change that makes sharding "add a
row, not rebuild" is to introduce a **tenant → shard** map *before* it's needed:
- a small table/config listing each tenant's Odoo cluster + DB host;
- nginx (or a thin router) directs `<tenant>.msolutions-eg.com` to that tenant's
  cluster from the map (today nginx has one upstream and `update_tenant_list.sh`
  regenerates one flat allow-list — extend it to emit an upstream per shard);
- provisioning picks the least-loaded shard and writes the row;
- backups already enumerate dynamically, so they follow the map for free.

With the map in place, adding a shard is: stand up another Odoo cluster (§10c
steps 11–18 point it at a DB host), then start writing new tenants' rows to it —
existing tenants never move. Without the map, a second host is a manual migration.
Build the routing table while there are 3 tenants, not 300.
