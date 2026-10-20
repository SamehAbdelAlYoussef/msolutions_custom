#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/odoo_cron_watchdog.sh  (authoritative — the timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/{deploy.env,backup.env}, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# odoo_cron_watchdog.sh — restart odoo_worker when its Odoo cron thread has died.
#
# WHY THIS EXISTS (2026-10-18): a transient connection timeout to the database
# host raised OperationalError inside odoo.service.cron.cron0. Odoo does not
# respawn a cron thread that died on an exception, and the worker runs with
# max_cron_threads = 1, so that single exception ended ALL cron processing. The
# container stayed "Up" for two days while every SaaS cron — provisioning,
# dropping, backups, and the stuck-tenant watchdog itself — did nothing. Every
# tenant created in that window sat in 'provisioning' forever.
#
# The signal is exact, not heuristic: Odoo keeps one OS thread per cron thread
# for the life of the process, sleeping between runs. /proc/1/task therefore
# holds 1 + max_cron_threads entries while cron is alive and collapses to 1 the
# moment the cron thread dies. No log parsing, no query, nothing to go stale.
#
# Read-only apart from the restart itself. It never touches a tenant.
set -uo pipefail

CONTAINER="${WATCHDOG_CONTAINER:-odoo_worker}"
MIN_TASKS=2            # PID 1 itself + at least one live cron thread
SETTLE_SECONDS=180     # a container still booting has not spawned cron yet

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)"
[ "$running" = "true" ] || exit 0    # stopped: docker's restart policy owns that case

started="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || echo '')"
if [ -n "$started" ]; then
  age=$(( $(date +%s) - $(date -d "$started" +%s 2>/dev/null || echo 0) ))
  [ "$age" -ge "$SETTLE_SECONDS" ] || exit 0
fi

# A failed exec (daemon busy, container mid-restart) must never trigger a restart.
tasks="$(docker exec "$CONTAINER" sh -c 'ls /proc/1/task | wc -l' 2>/dev/null)" || exit 0
case "$tasks" in ''|*[!0-9]*) exit 0 ;; esac
[ "$tasks" -ge "$MIN_TASKS" ] && exit 0

logger -t odoo-cron-watchdog \
  "cron thread gone in $CONTAINER (/proc/1/task = $tasks, expected >= $MIN_TASKS) — restarting"
docker restart "$CONTAINER" >/dev/null 2>&1
logger -t odoo-cron-watchdog "$CONTAINER restarted by the cron watchdog"
