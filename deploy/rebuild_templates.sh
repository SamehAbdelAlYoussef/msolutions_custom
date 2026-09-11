#!/usr/bin/env bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/rebuild_templates.sh  (authoritative — run manually).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/deploy.env, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# Rebuild every SaaS plan template from scratch: drop -> create -> install the
# plan's modules -> bake the ownership split -> mark IS_TEMPLATE / block
# connections -> stamp the rebuild time. Existing tenants are NOT touched.
#
# Templates go stale after every Odoo upgrade or module change -- run this then.
# The heavy lifting lives in saas.plan._rebuild_all_templates() (version
# controlled, unit tested); this wrapper just drives it inside the worker, which
# connects as odoo_provision (CREATEDB) and can see the filestore.
#
# Usage: rebuild_templates.sh
set -euo pipefail

[ -r /opt/scripts/deploy.env ] && . /opt/scripts/deploy.env
CONTROL_DB="${CONTROL_DB:?set CONTROL_DB in deploy.env}"
WORKER="${WORKER_CONTAINER:?set WORKER_CONTAINER in deploy.env}"

echo "=== rebuild templates $(date -u +%FT%TZ) ==="
out=$(docker exec -i "$WORKER" odoo shell -d "$CONTROL_DB" --no-http \
        --logfile=/dev/null 2>/dev/null <<'PY'
report = env['saas.plan']._rebuild_all_templates()
env.cr.commit()
for tpl, status, secs in report:
    print("TEMPLATE %-18s %-10s %ds" % (tpl, status, int(secs)))
PY
)
echo "$out"

if printf '%s\n' "$out" | grep -q 'FAILED'; then
    echo "=== FAILED: one or more templates did not rebuild ==="
    exit 1
fi
echo "=== done $(date -u +%FT%TZ) ==="
