#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/tenant_health.sh  (authoritative — the timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/{deploy.env,backup.env}, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# tenant_health.sh — host-side tenant reachability + orphan-DB consistency check
# for the msolutions_saas dashboard. Reconciles THREE facts in one cheap pass:
#   (a) intended-active tenants     : saas_tenant WHERE state=active, not suspended
#   (b) tenant databases that exist : pg_database (same predicate as routing sync)
#   (c) the public path is live     : GET https://<tenant>/web/health via nginx
#
# /web/health is auth='none', save_session=False -> no session is created and no
# tenant registry/DB is loaded. 200 = nginx routes the subdomain AND Odoo is up;
# nginx serves a 404 "workspace not found" page for any subdomain not in its map,
# so 200-vs-404 is a clean routing signal without touching the database.
#
# Writes an atomic JSON status file into a ROOT-OWNED dir inside the filestore
# that the container can READ but never WRITE (same posture as the backup
# manifest). It never changes routing, backups, or any tenant. Read-only.
set -euo pipefail

[ -r /opt/scripts/deploy.env ] && . /opt/scripts/deploy.env   # DB_HOST, CONTROL_DB, BASE_DOMAIN
[ -r /opt/scripts/backup.env ] && . /opt/scripts/backup.env   # FILESTORE (for the status dir)
: "${DB_HOST:?set DB_HOST in deploy.env}"; : "${DB_PORT:=5432}"
: "${CONTROL_DB:?set CONTROL_DB in deploy.env}"
: "${BASE_DOMAIN:?set BASE_DOMAIN in deploy.env}"
: "${FILESTORE:?set FILESTORE in backup.env}"

PROBE_TIMEOUT=4        # seconds per request
PROBE_PARALLEL=20      # bound wall-time at 300 tenants (worst case ~ 300/20 * 4s)

FS_ROOT="$(dirname "$FILESTORE")"            # Odoo data_dir on the host
STATUS_DIR="$FS_ROOT/.tenant_status"
OUT="$STATUS_DIR/tenant_health.json"
STATUS_GID="$(stat -c %g "$FILESTORE" 2>/dev/null || echo 101)"
# root:<container-gid> 0750 — the container group can read but not write, so a
# compromised Odoo cannot falsify tenant health (lists tenant names + orphans).
install -d -m 0750 -o root -g "$STATUS_GID" "$STATUS_DIR"

export PGPASSFILE=/opt/scripts/.pgpass PGSSLMODE=require
PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U odoo_provision -tA)

# (a) intended-active tenants, (all) managed records, (b) existing tenant DBs
ACTIVE=$("${PSQL[@]}" -d "$CONTROL_DB" -c \
  "SELECT name FROM saas_tenant WHERE state='active' AND COALESCE(suspended,false)=false ORDER BY name;")
ALLREC=$("${PSQL[@]}" -d "$CONTROL_DB" -c \
  "SELECT name FROM saas_tenant ORDER BY name;")
DBS=$("${PSQL[@]}" -d postgres -c \
  "SELECT datname FROM pg_database
   WHERE datname NOT IN ('postgres','template0','template1','$CONTROL_DB')
   AND datistemplate=false AND datallowconn ORDER BY datname;")

# (c) probe each active tenant's /web/health, in parallel, short timeout
probe_one() {
  local n="$1" code
  code=$(curl -s -o /dev/null -m "$PROBE_TIMEOUT" -k \
         --resolve "$n.$BASE_DOMAIN:443:127.0.0.1" \
         -w '%{http_code}' "https://$n.$BASE_DOMAIN/web/health" 2>/dev/null || true)
  [ -z "$code" ] && code=000     # connect/timeout failure -> nginx/Odoo down
  printf '%s\t%s\n' "$n" "$code"
}
export -f probe_one; export BASE_DOMAIN PROBE_TIMEOUT
PROBES=""
[ -n "$ACTIVE" ] && PROBES=$(printf '%s\n' $ACTIVE | grep -v '^$' \
  | xargs -r -P "$PROBE_PARALLEL" -I{} bash -c 'probe_one "$@"' _ {})

ACTIVE="$ACTIVE" ALLREC="$ALLREC" DBS="$DBS" PROBES="$PROBES" \
OUT="$OUT" STATUS_GID="$STATUS_GID" python3 <<'PY'
import os, json, datetime
OUT = os.environ["OUT"]
def lines(s): return [x for x in (os.environ.get(s,"") or "").splitlines() if x.strip()]
active = sorted(set(lines("ACTIVE")))
allrec = set(lines("ALLREC"))
dbs    = set(lines("DBS"))
codes  = {}
for row in lines("PROBES"):
    name, _, code = row.partition("\t")
    try: codes[name] = int(code)
    except ValueError: codes[name] = 0
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

checked = []
unreachable = 0
for n in active:
    has_db = n in dbs
    code = codes.get(n, 0)
    ok = has_db and code == 200
    if not ok: unreachable += 1
    checked.append({"tenant": n, "active": True, "has_db": has_db,
                    "http_code": code, "state": "ok" if ok else "unreachable",
                    "checked_at": now})

# orphan = a tenant DB with NO saas.tenant record at all (any state). A suspended
# or draft tenant still has a record, so it is managed, not orphan.
orphan_dbs = sorted(dbs - allrec)

manifest = {"generated_at": now, "active_count": len(active),
            "unreachable_count": unreachable, "orphan_count": len(orphan_dbs),
            "checked": checked, "orphan_dbs": orphan_dbs}
tmp = OUT + ".tmp"
with open(tmp, "w") as fh: json.dump(manifest, fh, indent=1)
os.chown(tmp, 0, int(os.environ.get("STATUS_GID", "101")))
os.chmod(tmp, 0o640)
os.replace(tmp, OUT)
print("wrote %s (%d active, %d unreachable, %d orphan)"
      % (OUT, len(active), unreachable, len(orphan_dbs)))
PY
