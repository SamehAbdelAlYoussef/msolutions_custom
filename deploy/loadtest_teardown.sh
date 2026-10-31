#!/bin/bash
# ── msolutions load-test teardown ─────────────────────────────────────────────
# Live copy: /opt/scripts/loadtest_teardown.sh  (mirror in deploy/, same-day rule)
#
# STANDALONE, IDEMPOTENT removal of EVERY trace of load-test tenants (name prefix
# "ltload"). Returns the system to its pre-test baseline. Safe to run at ANY
# moment — mid-test, after a crash, after a kill, after a reboot — and a no-op
# when nothing is left. One command:   sudo /opt/scripts/loadtest_teardown.sh
#
# It removes, in order:
#   1. saas_tenant rows          (control DB)      -> routing sync then drops them
#   2. tenant databases          (DB host)         -> RAW drop, WITH (FORCE); NO
#                                                      pre_drop backup, so test
#                                                      tenants never pollute B2 or
#                                                      the registry
#   3. per-tenant filestores     /opt/odoo/filestore/filestore/ltload*
#   4. backup artifacts across ALL THREE manifest sources (trap 4 in deploy/README)
#   5. orphaned staging          restore_staging / spool entries for ltload
#   6. regenerate manifest + reconcile registry + refresh nginx routing
# Then prints a residue report (every count must be 0).
#
# Raw psql runs as odoo_provision (owns tenant DBs, can drop/terminate). pg_hba
# on the DB host REQUIRES ssl, so PGSSLMODE=require + PGPASSFILE are set here
# explicitly (omitting them is the "auth failed / no encryption" trap).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

PREFIX="${LOADTEST_PREFIX:-ltload}"

# Hard safety guard: the prefix must be non-empty, at least 3 chars, and can
# NEVER be (or be a prefix of) a name we must keep.
case "$PREFIX" in
  ""|noveen|msolutions|nov*|mso*|postgres|tpl*|template*)
    echo "REFUSING: unsafe prefix '$PREFIX'"; exit 1;;
esac
if [ "${#PREFIX}" -lt 3 ]; then echo "REFUSING: prefix too short '$PREFIX'"; exit 1; fi
# noveen/msolutions must not match the LIKE pattern (belt-and-suspenders)
case "noveen" in $PREFIX*) echo "REFUSING: prefix matches noveen"; exit 1;; esac
case "msolutions" in $PREFIX*) echo "REFUSING: prefix matches msolutions"; exit 1;; esac

set -a; . /opt/scripts/deploy.env; set +a
export PGPASSFILE=/opt/scripts/.pgpass PGSSLMODE=require
PA="psql -h ${DB_HOST} -U odoo_provision -v ON_ERROR_STOP=0 -tAc"
FSD=/opt/odoo/filestore/filestore
LIKE="${PREFIX}%"

echo "== load-test teardown: prefix='${PREFIX}' =="

# 1. control-plane tenant rows. saas_tenant is referenced by FKs (audit_log,
#    backup*, restore_job, module_rel), so a bare DELETE fails. Prefer ORM
#    unlink (handles the ondelete cascade); fall back to raw FK-referrer
#    deletion when Odoo is down (crash/reboot teardown).
n_rows=$($PA "SELECT count(*) FROM saas_tenant WHERE name LIKE '${LIKE}'" -d "$CONTROL_DB")
if docker exec -i odoo_web odoo shell -c /etc/odoo/odoo.conf -d "$CONTROL_DB" --no-http >/dev/null 2>&1 <<PY
env["saas.tenant"].search([("name","like","${PREFIX}%")]).unlink(); env.cr.commit()
PY
then :; else
  for reft in saas_audit_log saas_backup saas_backup_download saas_backup_job saas_restore_job saas_tenant_module_rel; do
    $PA "DELETE FROM ${reft} WHERE tenant_id IN (SELECT id FROM saas_tenant WHERE name LIKE '${LIKE}')" -d "$CONTROL_DB" >/dev/null 2>&1
  done
  $PA "DELETE FROM saas_tenant WHERE name LIKE '${LIKE}'" -d "$CONTROL_DB" >/dev/null 2>&1
fi
rem=$($PA "SELECT count(*) FROM saas_tenant WHERE name LIKE '${LIKE}'" -d "$CONTROL_DB")
echo "1. saas_tenant rows: had ${n_rows}, remaining ${rem}"

# 2. databases -- RAW drop (WITH FORCE terminates connections), no pre_drop
dbs=$($PA "SELECT datname FROM pg_database WHERE datname LIKE '${LIKE}'" -d postgres)
dropped=0
for db in $dbs; do
  # extra guard per-name
  case "$db" in noveen|msolutions|postgres|tpl*|template*) echo "  SKIP protected: $db"; continue;; esac
  $PA "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${db}' AND pid<>pg_backend_pid()" -d postgres >/dev/null 2>&1
  if $PA "DROP DATABASE IF EXISTS \"${db}\" WITH (FORCE)" -d postgres >/dev/null 2>&1 \
     || $PA "DROP DATABASE IF EXISTS \"${db}\"" -d postgres >/dev/null 2>&1; then
    dropped=$((dropped+1))
  else
    echo "  WARN: could not drop ${db} (will retry on next run)"
  fi
done
echo "2. databases dropped: ${dropped}"

# 3. per-tenant filestores
fs_before=$(ls -d ${FSD}/${PREFIX}* 2>/dev/null | wc -l)
rm -rf ${FSD}/${PREFIX}* 2>/dev/null
echo "3. filestores removed: ${fs_before}"

# 4. backup artifacts -- all three manifest sources
rm -f /opt/odoo/filestore/pre_drop_backups/${PREFIX}* 2>/dev/null
rm -f /opt/backups/local/*/${PREFIX}*.sql /opt/backups/local/*/${PREFIX}*.filestore.tar.gz 2>/dev/null
rm -f /opt/odoo/filestore/.backup_status/manual/${PREFIX}*.json 2>/dev/null
echo "4. backup artifacts (pre_drop / local dumps / manual records) removed"

# 4b. clone template + LoadTest plan (named tpl_loadtest, outside the ltload prefix)
$PA "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='tpl_loadtest'" -d postgres >/dev/null 2>&1
$PA "DROP DATABASE IF EXISTS \"tpl_loadtest\" WITH (FORCE)" -d postgres >/dev/null 2>&1
rm -rf ${FSD}/tpl_loadtest 2>/dev/null
$PA "DELETE FROM saas_plan WHERE name='LoadTest' OR template_db='tpl_loadtest'" -d "$CONTROL_DB" >/dev/null 2>&1
echo "4b. clone template tpl_loadtest + LoadTest plan removed"

# 5. orphaned staging referencing ltload (best-effort; keyed by job, so match content)
grep -rl "${PREFIX}" /opt/odoo/filestore/restore_staging/ 2>/dev/null | xargs -r dirname 2>/dev/null | sort -u | while read -r d; do rm -rf "$d"; done
echo "5. staging leftovers referencing ${PREFIX} cleared"

# 6. regenerate manifest, reconcile registry, refresh routing
/opt/scripts/backup_manifest.sh >/dev/null 2>&1 && echo "6a. manifest regenerated" || echo "6a. manifest regen SKIPPED (restic unreachable)"
if docker exec -i odoo_web odoo shell -c /etc/odoo/odoo.conf -d "$CONTROL_DB" --no-http >/dev/null 2>&1 <<'PY'
env["saas.backup"]._sync_backups(); env.cr.commit()
PY
then echo "6b. registry reconciled"; else echo "6b. registry sync SKIPPED (odoo unreachable — cron will reconcile)"; fi
/opt/scripts/update_tenant_list.sh >/dev/null 2>&1 && echo "6c. routing refreshed" || echo "6c. routing refresh SKIPPED"

# 7. residue report -- every count MUST be 0
r_db=$($PA "SELECT count(*) FROM pg_database WHERE datname LIKE '${LIKE}'" -d postgres)
r_fs=$(ls -d ${FSD}/${PREFIX}* 2>/dev/null | wc -l)
r_rows=$($PA "SELECT count(*) FROM saas_tenant WHERE name LIKE '${LIKE}'" -d "$CONTROL_DB")
r_pd=$(ls /opt/odoo/filestore/pre_drop_backups/${PREFIX}* 2>/dev/null | wc -l)
r_ld=$(ls /opt/backups/local/*/${PREFIX}* 2>/dev/null | wc -l)
r_mr=$(ls /opt/odoo/filestore/.backup_status/manual/${PREFIX}* 2>/dev/null | wc -l)
r_route=$(grep -c "^${PREFIX}" /opt/odoo/conf.d/tenants.conf 2>/dev/null); r_route=${r_route:-0}
echo "== RESIDUE (all must be 0): db=${r_db} filestore=${r_fs} tenant_rows=${r_rows} predrop=${r_pd} localdumps=${r_ld} manual=${r_mr} routing=${r_route} =="
if [ "${r_db}${r_fs}${r_rows}${r_pd}${r_ld}${r_mr}${r_route}" = "0000000" ]; then
  echo "TEARDOWN CLEAN"
else
  echo "TEARDOWN INCOMPLETE — re-run (some resource was busy)"
fi
