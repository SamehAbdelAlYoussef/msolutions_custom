#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/backup_now.sh  (authoritative — the timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/{backup.env,deploy.env}, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# backup_now.sh — host side of "Backup Now". The Odoo module dumps a tenant
# (reusing its own pg_dump path) into the filestore and drops a spool request;
# this runner uploads that dump to B2 with restic (credentials stay HERE, never
# in a container), writes the authoritative manual-backup record the manifest
# reads, writes a result marker for the module, then DELETES the staged temp
# files -- on success AND on failure.
#
# It takes the SAME flock as the nightly backup (a file on the shared-inode
# filestore), so a manual upload and the nightly can never run at once. It runs
# NO `restic forget`: manual snapshots use a distinct path + `manual` tag, a
# different restic group, so they neither count toward nor get pruned by the
# nightly 7/4/3 policy.
set -euo pipefail

[ -r /opt/scripts/backup.env ] && . /opt/scripts/backup.env
: "${FILESTORE:?set FILESTORE in backup.env}"
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY in backup.env}"
: "${RESTIC_PASSWORD:?set RESTIC_PASSWORD in backup.env}"

FS_ROOT="$(dirname "$FILESTORE")"          # = Odoo data_dir (container /var/lib/odoo)
SPOOL="$FS_ROOT/.backup_spool/requests"    # container -> host
RESULTS="$FS_ROOT/.backup_spool/results"   # host -> container
MANUAL_REC="$FS_ROOT/.backup_status/manual"  # root-owned; the manifest reads it
LOCK="$FS_ROOT/.backup_locks/global.lock"
STATUS_GID="$(stat -c %g "$FILESTORE" 2>/dev/null || echo 101)"

# Host-authored dirs (container may read results, never write them). The lock
# dir/file must be openable O_RDWR by BOTH root and the container group. The
# spool parent is group-writable so the container can create requests/ under it.
install -d -m 0770 -o root -g "$STATUS_GID" "$FS_ROOT/.backup_spool"
install -d -m 0750 -o root -g "$STATUS_GID" "$RESULTS" "$MANUAL_REC"
install -d -m 0770 -o root -g "$STATUS_GID" "$(dirname "$LOCK")"
# Enforce lock perms every run so BOTH root (the nightly's flock) and the
# container group can always open it O_RDWR -- even if the nightly created it
# root:root first in a cold-start race.
[ -e "$LOCK" ] || : > "$LOCK"
chown root:"$STATUS_GID" "$LOCK" 2>/dev/null || true
chmod 0660 "$LOCK" 2>/dev/null || true

# Old result markers: the module's 1-min cron has long since consumed them.
find "$RESULTS" -type f -name '*.json' -mmin +10 -delete 2>/dev/null || true

[ -d "$SPOOL" ] || exit 0

restic_run() {   # credentials via exported env in a subshell -> never in `ps`
  ( export RESTIC_REPOSITORY="$RESTIC_REPOSITORY" RESTIC_PASSWORD="$RESTIC_PASSWORD" \
           AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
           AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
    restic "$@" )
}

write_json() {   # write_json <path> <python-dict-expr-in-$D>  (atomic, root:gid 0640)
  local path="$1" expr="$2"
  D="$expr" GID="$STATUS_GID" python3 - "$path" <<'PY'
import json, os, sys
path = sys.argv[1]; tmp = path + ".tmp"
open(tmp, "w").write(os.environ["D"])
os.chown(tmp, 0, int(os.environ["GID"])); os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
}

shopt -s nullglob
for req in "$SPOOL"/*.json; do
  mapfile -t F < <(python3 - "$req" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("\n\n\n\n\n"); raise SystemExit
for k in ("job_id", "tenant", "sql_rel", "fs_rel", "db_bytes", "fs_bytes"):
    print(d.get(k, ""))
PY
) || { rm -f "$req"; continue; }
  JOB="${F[0]}"; TENANT="${F[1]}"; SQLREL="${F[2]}"; FSREL="${F[3]}"; DB="${F[4]:-0}"; FSB="${F[5]:-0}"

  # Defense against a tampered spool from a compromised container: the tenant
  # name must be well-formed and the paths must be under manual_backups/ with no
  # traversal. Resolve the relative paths against the host filestore root.
  if ! printf '%s' "$JOB" | grep -qE '^[0-9]+$' \
     || ! printf '%s' "$TENANT" | grep -qE '^[a-z][a-z0-9]{2,30}$' \
     || ! printf '%s' "$SQLREL" | grep -qE '^manual_backups/[a-z0-9_.-]+$' \
     || { [ -n "$FSREL" ] && ! printf '%s' "$FSREL" | grep -qE '^manual_backups/[a-z0-9_.-]+$'; }; then
    echo "backup_now: rejecting malformed spool $req"; rm -f "$req"; continue
  fi
  SQL="$FS_ROOT/$SQLREL"; FS=""; [ -n "$FSREL" ] && FS="$FS_ROOT/$FSREL"
  # Orphaned spool (dump already gone -- e.g. the watchdog failed the job and
  # cleaned up): nothing to upload, drop the request.
  if [ ! -f "$SQL" ]; then rm -f "$req"; continue; fi

  (
    flock -w 3600 9 || { echo "backup_now: lock timeout for job $JOB"; exit 0; }

    files=("$SQL"); [ -n "$FS" ] && [ -f "$FS" ] && files+=("$FS")
    day="$(date -u +%F)"; now="$(date -u +%FT%H:%M:%SZ)"
    if out="$(restic_run backup "${files[@]}" \
                --tag manual --tag "manual-$TENANT" --tag "odoo-$day" 2>&1)"; then
      snap="$(printf '%s\n' "$out" | grep -oE 'snapshot [0-9a-f]{6,} saved' | awk '{print $2}' | head -1)"
      status=ok; reached=true; msg="Uploaded to B2"
      has_fs=false; [ -n "$FS" ] && has_fs=true
      # Authoritative manual record (root-owned) -> backup_manifest.sh -> registry.
      rec="$(TENANT="$TENANT" JOB="$JOB" NOW="$now" DB="$DB" FSB="$FSB" HAS_FS="$has_fs" SNAP="$snap" python3 -c '
import json, os
print(json.dumps({"id": "manual-%s-%s" % (os.environ["TENANT"], os.environ["JOB"]),
  "tenant": os.environ["TENANT"], "date": os.environ["NOW"], "source": "manual",
  "db_bytes": int(os.environ["DB"]), "fs_bytes": int(os.environ["FSB"]),
  "has_filestore": os.environ["HAS_FS"] == "true", "reached_b2": True,
  "restic_snapshot_id": os.environ["SNAP"] or None, "restic_repo": "daily"}))')"
      write_json "$MANUAL_REC/${TENANT}_${JOB}.json" "$rec"
    else
      snap=""; status=failed; reached=false
      msg="restic upload failed: $(printf '%s' "$out" | tail -c 300 | tr '\n' ' ')"
      echo "backup_now: job $JOB upload FAILED"
    fi

    # DELETE the staged temp files -- success OR failure. The B2 copy (on
    # success) is the durable one; a manual dump never lingers on local disk.
    rm -f "$SQL"; [ -n "$FS" ] && rm -f "$FS"

    res="$(JOB="$JOB" STATUS="$status" MSG="$msg" REACHED="$reached" SNAP="$snap" python3 -c '
import json, os
print(json.dumps({"job_id": int(os.environ["JOB"]), "status": os.environ["STATUS"],
  "message": os.environ["MSG"], "reached_b2": os.environ["REACHED"] == "true",
  "restic_snapshot_id": os.environ["SNAP"] or None}))')"
    write_json "$RESULTS/${JOB}.json" "$res"
    rm -f "$req"
  ) 9>"$LOCK"
done
