#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/restic_prune.sh  (authoritative — the weekly timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/backup.env, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# Weekly space reclamation. Expensive -> not run nightly.
set -a; . /opt/scripts/backup.env; set +a
export RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
: "${LOG_DIR:?set LOG_DIR in backup.env}"; : "${LOCAL_DIR:?set LOCAL_DIR in backup.env}"
BACKUP_ROOT="$(dirname "$LOCAL_DIR")"          # holds LAST_PRUNE_OK / _FAILED
LOG="$LOG_DIR/prune-$(date -u +%F_%H%M%S).log"
exec > "$LOG" 2>&1
echo "START $(date -u +%T)"
restic prune; rc=$?
echo "EXIT=$rc"
if [ "$rc" -ne 0 ]; then
  echo "$(date -u +%FT%TZ) restic prune FAILED (see $LOG)" >> "$BACKUP_ROOT/LAST_PRUNE_FAILED"
  [ -n "${ALERT_WEBHOOK:-}" ] && curl -fsS -m10 -X POST "$ALERT_WEBHOOK" -H 'Content-Type: application/json' -d '{"text":"Odoo restic prune FAILED"}' >/dev/null 2>&1 || true
else
  rm -f "$BACKUP_ROOT/LAST_PRUNE_FAILED"; date -u +%FT%TZ > "$BACKUP_ROOT/LAST_PRUNE_OK"
fi
echo "END $(date -u +%T)"
