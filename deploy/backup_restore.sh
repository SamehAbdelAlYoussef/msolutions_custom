#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/backup_restore.sh  (authoritative — the timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/backup.env, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# backup_restore.sh — host side of "Download an EXISTING backup". The module
# writes a restore request (id, tenant, source, date, snapshot_id) into a spool;
# this runner assembles that backup's dump.sql + filestore/ into the download
# staging dir the container then streams. B2/restic credentials stay HERE.
#
# Fast path: if the backup is still on local disk (<7 days) it is copied
# directly. Otherwise `restic restore` pulls it from B2 (checksum-verified). It
# takes the SAME flock as the nightly + Backup Now so it never runs alongside a
# dump. It is read-only w.r.t. the backups themselves -- it only reads them.
set -euo pipefail

[ -r /opt/scripts/backup.env ] && . /opt/scripts/backup.env
: "${FILESTORE:?set FILESTORE in backup.env}"
: "${LOCAL_DIR:?set LOCAL_DIR in backup.env}"
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY in backup.env}"
: "${RESTIC_PASSWORD:?set RESTIC_PASSWORD in backup.env}"

FS_ROOT="$(dirname "$FILESTORE")"
PREDROP="$FS_ROOT/pre_drop_backups"
SPOOL="$FS_ROOT/.backup_spool/restore_requests"    # container -> host
RESULTS="$FS_ROOT/.backup_spool/restore_results"   # host -> container
LOCK="$FS_ROOT/.backup_locks/global.lock"
STATUS_GID="$(stat -c %g "$FILESTORE" 2>/dev/null || echo 101)"

install -d -m 0770 -o root -g "$STATUS_GID" "$FS_ROOT/.backup_spool"
install -d -m 0750 -o root -g "$STATUS_GID" "$RESULTS"
install -d -m 0770 -o root -g "$STATUS_GID" "$(dirname "$LOCK")"
[ -e "$LOCK" ] || : > "$LOCK"; chown root:"$STATUS_GID" "$LOCK" 2>/dev/null || true; chmod 0660 "$LOCK" 2>/dev/null || true
find "$RESULTS" -type f -name '*.json' -mmin +10 -delete 2>/dev/null || true
[ -d "$SPOOL" ] || exit 0

restic_run() {   # creds via exported env in a subshell -> never in `ps`
  ( export RESTIC_REPOSITORY="$RESTIC_REPOSITORY" RESTIC_PASSWORD="$RESTIC_PASSWORD" \
           AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
           AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
    restic "$@" )
}

result() {   # id status message
  local id="$1" status="$2" msg="$3"
  ID="$id" ST="$status" MSG="$msg" GID="$STATUS_GID" RES="$RESULTS" python3 - <<'PY'
import json, os
p = os.path.join(os.environ["RES"], "%s.json" % os.environ["ID"]); tmp = p + ".tmp"
open(tmp, "w").write(json.dumps({"id": int(os.environ["ID"]), "status": os.environ["ST"],
                                 "message": os.environ["MSG"]}))
os.chown(tmp, 0, int(os.environ["GID"])); os.chmod(tmp, 0o640); os.replace(tmp, p)
PY
}

shopt -s nullglob
for req in "$SPOOL"/*.json; do
  mapfile -t F < <(python3 - "$req" <<'PY'
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception: print("\n\n\n\n\n\n"); raise SystemExit
for k in ("id","tenant","staging_rel","ref_key","source","date","snapshot_id"):
    print(d.get(k,""))
PY
) || { rm -f "$req"; continue; }
  ID="${F[0]}"; TENANT="${F[1]}"; SREL="${F[2]}"; REF="${F[3]}"; SRC="${F[4]}"; DATE="${F[5]}"; SNAP="${F[6]}"

  if ! printf '%s' "$ID" | grep -qE '^[0-9]+$' \
     || ! printf '%s' "$TENANT" | grep -qE '^[a-z][a-z0-9]{2,30}$' \
     || [ "${SREL#downloads/}" = "$SREL" ]; then
    echo "backup_restore: rejecting malformed spool $req"; rm -f "$req"; continue
  fi
  STAGING="$FS_ROOT/$SREL"

  (
    flock -w 3600 9 || { echo "backup_restore: lock timeout for $ID"; exit 0; }
    rm -rf "$STAGING"; mkdir -p "$STAGING/filestore"
    t0=$(date +%s)

    LSQL=""; LFS=""; base=""
    case "$SRC" in
      nightly)  LSQL="$LOCAL_DIR/$DATE/$TENANT.sql"; LFS="$LOCAL_DIR/$DATE/$TENANT.filestore.tar.gz" ;;
      pre_drop) base="${REF#predrop-}"; LSQL="$PREDROP/$base.sql"; LFS="$PREDROP/$base.filestore.tar.gz" ;;
      manual)   : ;;   # local copy was deleted after upload -> restic only
    esac

    origin="restic"
    if [ -n "$LSQL" ] && [ -f "$LSQL" ]; then
      origin="local"
      cp "$LSQL" "$STAGING/dump.sql"
      [ -f "$LFS" ] && tar xzf "$LFS" -C "$STAGING/filestore" --strip-components=1
    elif [ -n "$SNAP" ]; then
      tmp="$(mktemp -d)"
      case "$SRC" in
        nightly)  inc=(--include "*/$TENANT.sql" --include "*/$TENANT.filestore.tar.gz") ;;
        pre_drop) inc=(--include "*${base}*") ;;
        *)        inc=(--include "*${TENANT}_*") ;;
      esac
      if restic_run restore "$SNAP" --target "$tmp" "${inc[@]}" >/tmp/rest.$ID.log 2>&1; then
        rsql="$(find "$tmp" -type f -name '*.sql' | head -1)"
        rfs="$(find "$tmp" -type f -name '*.filestore.tar.gz' | head -1)"
        if [ -n "$rsql" ]; then
          cp "$rsql" "$STAGING/dump.sql"
          [ -n "$rfs" ] && tar xzf "$rfs" -C "$STAGING/filestore" --strip-components=1
        fi
      fi
      rm -rf "$tmp" /tmp/rest.$ID.log
    fi

    dt=$(( $(date +%s) - t0 ))
    if [ -f "$STAGING/dump.sql" ] && [ "$(stat -c%s "$STAGING/dump.sql")" -ge 4096 ]; then
      echo "backup_restore: $ID staged from $origin in ${dt}s"
      result "$ID" ok "restored from $origin in ${dt}s"
    else
      rm -rf "$STAGING"
      result "$ID" failed "could not locate/restore backup $REF"
    fi
    rm -f "$req"
  ) 9>"$LOCK"
done
