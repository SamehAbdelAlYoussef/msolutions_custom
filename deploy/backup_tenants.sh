#!/bin/bash
# ── msolutions ops script — SCRUBBED REFERENCE COPY (NOT the running script) ──
# Live / authoritative copy: /opt/scripts/backup_tenants.sh on the Odoo host.
# That copy is tested and the ENTIRE backup chain depends on it. It is
# intentionally NOT byte-identical to this file — the live copy keeps a couple
# of hardcoded infrastructure defaults for resilience. DO NOT deploy this file
# over the live one.
# This repo copy is scrubbed for the PUBLIC repo: infra values (DB host, paths)
# are read from /opt/scripts/backup.env instead of being hardcoded.
# RULE: any change to the live script must be mirrored here the SAME DAY
# (see deploy/README.md). deploy/check_drift.sh compares the logic and ignores
# the intentionally-scrubbed infra lines.
# ─────────────────────────────────────────────────────────────────────────────
#
# Per-tenant backup: pg_dump -Fp + filestore -> local(7d) + restic->B2(7/4/3).
# Usage: backup_tenants.sh [tenant]   (no arg = every tenant)
set -uo pipefail
ENVF=/opt/scripts/backup.env
[ -r "$ENVF" ] && . "$ENVF"
: "${DB_HOST:?set DB_HOST in backup.env}"; : "${DB_PORT:=5432}"; : "${DB_USER:=odoo_web}"
: "${FILESTORE:?set FILESTORE in backup.env}"
: "${LOCAL_DIR:?set LOCAL_DIR in backup.env}"; : "${LOG_DIR:?set LOG_DIR in backup.env}"; : "${LOCAL_KEEP_DAYS:=7}"
BACKUP_ROOT="$(dirname "$LOCAL_DIR")"          # holds LAST_BACKUP_OK / _FAILED
DATE=$(date -u +%F); TS=$(date -u +%F_%H%M%S); LOG="$LOG_DIR/backup-$TS.log"
mkdir -p "$LOCAL_DIR/$DATE" "$LOG_DIR"; exec > >(tee -a "$LOG") 2>&1
fail=0
alert(){ echo "ALERT: $*"; echo "$(date -u +%FT%TZ) $*" >> "$BACKUP_ROOT/LAST_BACKUP_FAILED"
  msg="Odoo backup FAILED on $(hostname): $*"
  # generic webhook (kept for compatibility)
  [ -n "${ALERT_WEBHOOK:-}" ] && curl -fsS -m 10 -X POST "$ALERT_WEBHOOK" -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 || true
  # Telegram: sendMessage API (chat_id + text, url-encoded)
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] && \
    curl -fsS -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
}
echo "=== backup start $TS ==="
export PGPASSWORD="${PGPASSWORD:-}"
CONN="host=$DB_HOST port=$DB_PORT user=$DB_USER sslmode=require"
if [ $# -ge 1 ]; then TENANTS="$1"; else
  TENANTS=$(psql "$CONN dbname=postgres" -tAc "SELECT datname FROM pg_database WHERE has_database_privilege('$DB_USER',datname,'CONNECT') AND NOT datistemplate AND datallowconn AND datname<>'postgres' ORDER BY 1;") || { alert "cannot list tenants"; exit 1; }
fi
for T in $TENANTS; do
  echo "--- $T ---"
  if pg_dump "$CONN dbname=$T" -Fp -f "$LOCAL_DIR/$DATE/$T.sql"; then
    sz=$(stat -c%s "$LOCAL_DIR/$DATE/$T.sql"); echo "  db dump ok ($sz bytes)"
    [ "$sz" -lt 1000 ] && { alert "$T dump too small"; fail=1; }
  else
    rm -f "$LOCAL_DIR/$DATE/$T.sql"   # discard partial dump; never treat as good
    alert "pg_dump FAILED for $T (partial dump discarded)"; fail=1
  fi
  if [ -d "$FILESTORE/$T" ]; then
    tar czf "$LOCAL_DIR/$DATE/$T.filestore.tar.gz" -C "$FILESTORE" "$T" || { alert "filestore tar failed for $T"; fail=1; }
    echo "  filestore ok"
  else echo "  (no filestore dir)"; fi
done
find "$LOCAL_DIR" -maxdepth 1 -type d -name '20*' -mtime +$LOCAL_KEEP_DAYS -exec rm -rf {} \; 2>/dev/null
PREDROP="$(dirname "$FILESTORE")/pre_drop_backups"
find "$PREDROP" -type f -mtime +$LOCAL_KEEP_DAYS -delete 2>/dev/null || true
# ===== Tier 2a: repo_daily (prunable; forget nightly, prune weekly) =====
if [ -n "${RESTIC_REPOSITORY:-}" ] && [ -n "${RESTIC_PASSWORD:-}" ]; then
  if env RESTIC_REPOSITORY="$RESTIC_REPOSITORY" RESTIC_PASSWORD="$RESTIC_PASSWORD" \
         AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
         restic backup "$LOCAL_DIR/$DATE" "$PREDROP" --tag "odoo-$DATE"; then
    env RESTIC_REPOSITORY="$RESTIC_REPOSITORY" RESTIC_PASSWORD="$RESTIC_PASSWORD" \
        AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
        restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 || { alert "repo_daily forget failed"; fail=1; }
    echo "  repo_daily ok"
  else alert "repo_daily backup failed"; fail=1; fi
else alert "repo_daily not configured - OFFSITE SKIPPED"; fail=1; fi

# ===== Tier 2b: repo_locked (append-only, Object Lock GOVERNANCE; NEVER pruned) =====
# --no-lock => no lock files => no delete needed => works with Object Lock immutability.
if [ -n "${LOCKED_AWS_ACCESS_KEY_ID:-}" ] && [ -n "${RESTIC_PASSWORD_LOCKED:-}" ]; then
  if env RESTIC_REPOSITORY="$RESTIC_REPOSITORY_LOCKED" RESTIC_PASSWORD="$RESTIC_PASSWORD_LOCKED" \
         AWS_ACCESS_KEY_ID="$LOCKED_AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$LOCKED_AWS_SECRET_ACCESS_KEY" \
         restic backup --no-lock "$LOCAL_DIR/$DATE" "$PREDROP" --tag "odoo-$DATE"; then
    echo "  repo_locked ok (append-only, no prune)"
  else alert "repo_locked backup failed"; fail=1; fi
else
  echo "  repo_locked NOT YET CONFIGURED (staged; awaiting writer key) -- not failing job yet"
fi
if [ "$fail" = 0 ]; then rm -f "$BACKUP_ROOT/LAST_BACKUP_FAILED"; date -u +%FT%TZ > "$BACKUP_ROOT/LAST_BACKUP_OK"; echo "=== OK ==="
else echo "=== FAILED (see $LOG) ==="; fi
exit $fail
