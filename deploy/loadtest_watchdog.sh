#!/bin/bash
# ── msolutions load-test WATCHDOG ─────────────────────────────────────────────
# Live copy: /opt/scripts/loadtest_watchdog.sh  (mirror in deploy/, same-day rule)
#
# Independent safety monitor for the load test. Samples the two binding limits on
# the DB host every INTERVAL seconds and, if either ceiling is reached, writes an
# ABORT flag that the generator MUST check before every provision. It also prints
# a line per sample so it can run under a Monitor for live visibility.
#
#   DISK  : sum(pg_database_size) >= DISK_ABORT_GB (default 40 GB)
#   CONNS : count(pg_stat_activity) >= CONN_ABORT   (default 400 of 500)
#
# The generator's OWN per-tenant pre-flight is the hard guarantee (synchronous,
# cannot overshoot). This watchdog is the second line: it catches runaway growth
# between provisions and gives a single kill signal (the flag) + a visible trail.
#
# pg_hba on the DB host requires SSL -> PGSSLMODE=require + PGPASSFILE set here.
# Read-only: it never creates, drops, or changes anything; it only observes and
# writes the local flag file. Stop it with Ctrl-C / TaskStop; it exits on ABORT.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DISK_ABORT_GB="${DISK_ABORT_GB:-40}"
CONN_ABORT="${CONN_ABORT:-400}"
RAM_ABORT_MB="${RAM_ABORT_MB:-2048}"   # app-host MemAvailable floor; below this, noveen is at risk
RAM_WARN_MB="${RAM_WARN_MB:-4096}"     # early-warning band (logged, not fatal)
INTERVAL="${INTERVAL:-5}"
ABORT_FLAG="${ABORT_FLAG:-/opt/scripts/.loadtest_ABORT}"

set -a; . /opt/scripts/deploy.env; set +a
export PGPASSFILE=/opt/scripts/.pgpass PGSSLMODE=require
Q(){ psql -h "$DB_HOST" -U odoo_provision -d postgres -tAc "$1" 2>/dev/null; }

DISK_ABORT_BYTES=$(( DISK_ABORT_GB * 1024 * 1024 * 1024 ))
echo "watchdog armed: disk_abort=${DISK_ABORT_GB}GB conn_abort=${CONN_ABORT} ram_abort=${RAM_ABORT_MB}MB interval=${INTERVAL}s flag=${ABORT_FLAG}"
rm -f "$ABORT_FLAG"

while true; do
  pg_bytes=$(Q "SELECT COALESCE(sum(pg_database_size(datname)),0) FROM pg_database")
  conns=$(Q "SELECT count(*) FROM pg_stat_activity")
  lt=$(Q "SELECT count(*) FROM pg_database WHERE datname LIKE 'ltload%'")
  ram_avail=$(free -m | awk "/^Mem:/{print \$7}")   # app-host available MB (reclaimable incl.)
  # guard against transient query failure (SSL/hba/etc): skip the sample, do NOT abort
  if [ -z "$pg_bytes" ] || [ -z "$conns" ] || [ -z "$ram_avail" ]; then
    echo "$(date +%H:%M:%S) SAMPLE-FAILED (transient) — holding"
    sleep "$INTERVAL"; continue
  fi
  pg_gb=$(awk "BEGIN{printf \"%.2f\", $pg_bytes/1073741824}")
  ts=$(date +%H:%M:%S)
  reason=""
  [ "$pg_bytes" -ge "$DISK_ABORT_BYTES" ] && reason="DISK ${pg_gb}GB >= ${DISK_ABORT_GB}GB"
  [ "$conns" -ge "$CONN_ABORT" ] && reason="${reason:+$reason; }CONNS ${conns} >= ${CONN_ABORT}"
  [ "$ram_avail" -le "$RAM_ABORT_MB" ] && reason="${reason:+$reason; }RAM ${ram_avail}MB <= ${RAM_ABORT_MB}MB"
  if [ -n "$reason" ]; then
    echo "ABORT ${ts} ${reason} (ltload_dbs=${lt})"
    echo "$reason" > "$ABORT_FLAG"
    exit 2
  fi
  warn=""
  [ "$ram_avail" -le "$RAM_WARN_MB" ] && warn=" WARN-ram-low"
  echo "${ts} ok pg=${pg_gb}GB conns=${conns} ram_avail=${ram_avail}MB ltload_dbs=${lt}${warn}"
  sleep "$INTERVAL"
done
