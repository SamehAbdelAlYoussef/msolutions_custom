#!/usr/bin/env bash
# check_drift.sh — warn if a live /opt/scripts/* ops script has diverged from
# its committed deploy/ reference copy. Cheap, read-only, no secrets touched.
#
#   MIRRORED scripts are kept byte-identical to their live copy -> exact diff.
#   backup_tenants.sh is intentionally NOT identical (the live copy keeps a few
#     hardcoded infra values). For it we compare the LOGIC ONLY: both files are
#     normalized so the scrubbed infra lines collapse to the same canonical form,
#     then diffed. A clean result means the executable logic matches; a non-empty
#     result means real drift a human must reconcile. This is best-effort by
#     design — treat a warning as "look", not "the sky is falling".
#
# Exit 0 = no drift found; exit 1 = drift (or a file missing).
set -uo pipefail

LIVE_DIR="${LIVE_DIR:-/opt/scripts}"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MIRRORED="backup_manifest.sh update_tenant_list.sh rebuild_templates.sh restic_prune.sh"
REFERENCE="backup_tenants.sh"

rc=0

# Canonicalize a scrubbed/live pair down to comparable logic:
#  - drop shebang, comments and blank lines
#  - collapse ${VAR:=default} and ${VAR:?msg} to bare ${VAR} (env-default lines)
#  - map the scrubbed infra paths (live literals AND their derived forms) to tags
#  - drop the deploy-only BACKUP_ROOT helper assignment
#  - strip double-quotes and squeeze whitespace
normalize() {
  sed -E \
    -e '/^#!/d' -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
    -e 's/:=[^}]*\}/}/g' -e 's/:\?[^}]*\}/}/g' \
    -e 's#/opt/odoo/filestore/pre_drop_backups#@PREDROP@#g' \
    -e 's#\$\(dirname "\$FILESTORE"\)/pre_drop_backups#@PREDROP@#g' \
    -e 's#/opt/backups#@BR@#g' \
    -e 's#\$\{?BACKUP_ROOT\}?#@BR@#g' \
    -e '/^BACKUP_ROOT=/d' \
    -e 's/"//g' \
    -e 's/[[:space:]]+/ /g' -e 's/^ //' -e 's/ $//' "$1"
}

echo "== mirrored (must be byte-identical) =="
for f in $MIRRORED; do
  if [ ! -f "$LIVE_DIR/$f" ]; then echo "  MISSING live   $f"; rc=1; continue; fi
  if [ ! -f "$DEPLOY_DIR/$f" ]; then echo "  MISSING deploy $f"; rc=1; continue; fi
  if diff -q "$LIVE_DIR/$f" "$DEPLOY_DIR/$f" >/dev/null; then
    echo "  OK    $f"
  else
    echo "  DRIFT $f  (live and deploy differ; mirror the change)"; rc=1
  fi
done

echo "== reference (logic compared, scrubbed infra ignored) =="
for f in $REFERENCE; do
  if [ ! -f "$LIVE_DIR/$f" ] || [ ! -f "$DEPLOY_DIR/$f" ]; then
    echo "  MISSING $f"; rc=1; continue; fi
  if diff <(normalize "$LIVE_DIR/$f") <(normalize "$DEPLOY_DIR/$f") >/dev/null; then
    echo "  OK    $f  (logic matches; only scrubbed infra lines differ)"
  else
    echo "  DRIFT $f  (logic differs — review below):"; rc=1
    diff <(normalize "$LIVE_DIR/$f") <(normalize "$DEPLOY_DIR/$f") | sed 's/^/      /'
  fi
done

[ "$rc" = 0 ] && echo "== no drift ==" || echo "== drift found (see above) =="
exit $rc
