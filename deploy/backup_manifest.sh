#!/bin/bash
# ── msolutions ops script — version-controlled in msolutions_custom/deploy/ ──
# Live copy: /opt/scripts/backup_manifest.sh  (authoritative — the timer runs it).
# This repo copy is kept BYTE-IDENTICAL to the live copy; all infrastructure
# values come from /opt/scripts/backup.env, never hardcoded here.
# RULE: any change to the live script must be mirrored into deploy/ the SAME DAY
# (see deploy/README.md). Run deploy/check_drift.sh to detect divergence.
# ─────────────────────────────────────────────────────────────────────────────
#
# backup_manifest.sh — host-side, READ-ONLY generator for the msolutions_saas
# backup registry. Reads local dumps, pre-drop dumps, the OK/FAILED markers, the
# newest run log, and `restic snapshots` (host-side, --no-lock). Writes an atomic
# JSON manifest into a ROOT-OWNED status dir inside the filestore that the
# container can READ but never WRITE. Never runs a backup; never changes
# backup_tenants.sh, its timer, or the schedule.
#
# FAILURE POLICY: if a *configured* restic repo cannot be listed, or any step
# errors, the script ABORTS WITHOUT WRITING — the previous manifest is left to
# age, so the panel goes STALE rather than false-green.
set -euo pipefail

ENVF=/opt/scripts/backup.env
# Source creds into THIS shell only. They are handed to restic via `export`
# inside a subshell (never on a command line -> never visible in `ps`).
if [ -r "$ENVF" ]; then . "$ENVF"; fi
: "${FILESTORE:?set FILESTORE in backup.env}"
: "${LOCAL_DIR:?set LOCAL_DIR in backup.env}"
: "${LOG_DIR:?set LOG_DIR in backup.env}"
RESTIC_TIMEOUT=120

# Derive layout from backup.env so no path is hardcoded here.
FS_ROOT="$(dirname "$FILESTORE")"            # Odoo data_dir on the host
BACKUP_ROOT="$(dirname "$LOCAL_DIR")"        # holds LAST_BACKUP_OK / _FAILED
PREDROP="$FS_ROOT/pre_drop_backups"
STATUS_DIR="$FS_ROOT/.backup_status"
OUT="$STATUS_DIR/backup_manifest.json"

# The container's odoo group (the gid it writes the filestore as). The manifest
# is readable only by root and this group -- not world-readable, since it lists
# tenant names, data sizes and backup posture.
STATUS_GID="$(stat -c %g "$FILESTORE" 2>/dev/null || echo 101)"

# root:<container-gid> 0750 -> owner root rwx, group r-x, others nothing. The
# container (that group) can traverse+read but cannot create, overwrite, or
# delete anything inside it, so a compromised Odoo cannot falsify the manifest.
install -d -m 0750 -o root -g "$STATUS_GID" "$STATUS_DIR"

# List a restic repo's snapshots as JSON on stdout.
#   unconfigured / staged repo (missing repo/pass/key) -> "[]" and success
#   configured repo that fails or times out -> non-zero (caller aborts, no write)
# --no-lock: read-only, takes no lock, can't block or conflict with the nightly
# backup or the weekly prune. stderr dropped so a restic error can't put the
# repository URL into the systemd journal.
snap_json() {
  local repo="$1" pass="$2" akey="$3" skey="$4"
  if [ -z "$repo" ] || [ -z "$pass" ] || [ -z "$akey" ]; then echo '[]'; return 0; fi
  ( export RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD="$pass" \
           AWS_ACCESS_KEY_ID="$akey" AWS_SECRET_ACCESS_KEY="$skey"
    timeout "$RESTIC_TIMEOUT" restic snapshots --no-lock --json 2>/dev/null )
}

SNAP_DAILY="$(mktemp)"; SNAP_LOCKED="$(mktemp)"
trap 'rm -f "$SNAP_DAILY" "$SNAP_LOCKED"' EXIT

if ! snap_json "${RESTIC_REPOSITORY:-}" "${RESTIC_PASSWORD:-}" \
               "${AWS_ACCESS_KEY_ID:-}" "${AWS_SECRET_ACCESS_KEY:-}" > "$SNAP_DAILY"; then
  echo "restic (daily) unreachable — leaving the previous manifest to age" >&2; exit 1
fi
if ! snap_json "${RESTIC_REPOSITORY_LOCKED:-}" "${RESTIC_PASSWORD_LOCKED:-}" \
               "${LOCKED_AWS_ACCESS_KEY_ID:-}" "${LOCKED_AWS_SECRET_ACCESS_KEY:-}" > "$SNAP_LOCKED"; then
  echo "restic (locked) unreachable — leaving the previous manifest to age" >&2; exit 1
fi

LOCAL_DIR="$LOCAL_DIR" PREDROP="$PREDROP" LOG_DIR="$LOG_DIR" BACKUP_ROOT="$BACKUP_ROOT" \
SNAP_DAILY="$SNAP_DAILY" SNAP_LOCKED="$SNAP_LOCKED" OUT="$OUT" STATUS_GID="$STATUS_GID" \
python3 <<'PY'
import os, json, glob, re, datetime
LOCAL_DIR=os.environ["LOCAL_DIR"]; PREDROP=os.environ["PREDROP"]
LOG_DIR=os.environ["LOG_DIR"]; OUT=os.environ["OUT"]; BACKUP_ROOT=os.environ["BACKUP_ROOT"]

def snap_dates(path):
    """{ 'YYYY-MM-DD': short_snapshot_id } from tags 'odoo-<date>'. Snapshot ids
    are non-secret identifiers; the repo URL/bucket/creds are never read here."""
    out={}
    try:
        for s in json.load(open(path)):
            for tag in s.get("tags") or []:
                m=re.match(r"odoo-(\d{4}-\d{2}-\d{2})$", tag)
                if m: out[m.group(1)]=s.get("short_id") or (s.get("id") or "")[:8]
    except Exception:
        pass
    return out
b2_daily=snap_dates(os.environ["SNAP_DAILY"]); b2_locked=snap_dates(os.environ["SNAP_LOCKED"])
def b2_for(day):
    if day in b2_daily:  return True, b2_daily[day],  "daily"
    if day in b2_locked: return True, b2_locked[day], "locked"
    return False, None, None

backups=[]
for sql in sorted(glob.glob(os.path.join(LOCAL_DIR,"20*","*.sql"))):
    day=os.path.basename(os.path.dirname(sql)); tenant=os.path.basename(sql)[:-4]
    fs=sql[:-4]+".filestore.tar.gz"; has_fs=os.path.exists(fs); reached,snap,repo=b2_for(day)
    backups.append({"id":"nightly-%s-%s"%(tenant,day),"tenant":tenant,
        "date":day+"T02:30:00Z","source":"nightly","db_bytes":os.path.getsize(sql),
        "fs_bytes":os.path.getsize(fs) if has_fs else 0,"has_filestore":has_fs,
        "reached_b2":reached,"restic_snapshot_id":snap,"restic_repo":repo})
for sql in sorted(glob.glob(os.path.join(PREDROP,"*.sql"))):
    base=os.path.basename(sql)[:-4]; m=re.match(r"(.+)_(\d{8})_(\d{6})$", base)
    if not m: continue
    tenant,d,t=m.group(1),m.group(2),m.group(3); day="%s-%s-%s"%(d[:4],d[4:6],d[6:])
    fs=sql[:-4]+".filestore.tar.gz"; has_fs=os.path.exists(fs); reached,snap,repo=b2_for(day)
    backups.append({"id":"predrop-%s"%base,"tenant":tenant,
        "date":"%sT%s:%s:%sZ"%(day,t[:2],t[2:4],t[4:]),"source":"pre_drop",
        "db_bytes":os.path.getsize(sql),"fs_bytes":os.path.getsize(fs) if has_fs else 0,
        "has_filestore":has_fs,"reached_b2":reached,"restic_snapshot_id":snap,"restic_repo":repo})

def read(p,cap=500):
    try: return open(p).read().strip()[:cap]
    except Exception: return ""
failed=[]; logs=sorted(glob.glob(os.path.join(LOG_DIR,"backup-*.log")))
if logs:
    txt=read(logs[-1],cap=200000)
    for m in re.finditer(r"pg_dump FAILED for (\S+)|(\S+) dump too small", txt):
        failed.append(m.group(1) or m.group(2))

manifest={"generated_at":datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "last_backup_ok":read(os.path.join(BACKUP_ROOT,"LAST_BACKUP_OK")),
    "last_backup_failed":read(os.path.join(BACKUP_ROOT,"LAST_BACKUP_FAILED")),
    "failed_tenants":sorted(set(failed)),"backups":backups}
tmp=OUT+".tmp"
with open(tmp,"w") as fh: json.dump(manifest,fh,indent=1)
os.chown(tmp,0,int(os.environ.get("STATUS_GID","101")))
os.chmod(tmp,0o640)
os.replace(tmp,OUT)
print("wrote %s (%d backups)"%(OUT,len(backups)))
PY
