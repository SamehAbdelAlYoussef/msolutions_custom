# Backup manifest contract

The backup **registry** (`saas.backup`) is read-only. The module never talks to
Backblaze B2 or restic, and no B2 credentials ever enter an Odoo container — if
Odoo is compromised, the attacker must not get a copy of every customer's data.

Instead, a small **host-side generator** (which holds the B2 creds, exactly like
`backup_tenants.sh`) writes a JSON manifest into the Odoo `data_dir`
(`/opt/odoo/filestore/backup_manifest.json` → the container reads it at
`/var/lib/odoo/backup_manifest.json`). The module only reads that file.

`backup_tenants.sh`, its systemd timer, and the B2 credentials are **not
touched**. The generator is a separate reader on its own timer.

## What the generator does (host, has creds)

1. Scan `/opt/backups/local/<date>/<tenant>.sql` + `.filestore.tar.gz`
   → source `nightly`, with sizes and dates.
2. Scan `/opt/odoo/filestore/pre_drop_backups/<tenant>_<ts>.*` → source `pre_drop`.
3. Read `/opt/backups/LAST_BACKUP_OK` / `LAST_BACKUP_FAILED`; parse the newest
   `/opt/backups/logs/backup-*.log` for per-tenant failures
   (`pg_dump FAILED for <T>`, `<T> dump too small`).
4. `restic snapshots --json` on **both** repos (daily + locked) to attach
   snapshot ids and mark `reached_b2`.
5. Write the manifest atomically (temp file + rename) to the data_dir.

Manual backups (Section 2) will register themselves directly; the generator
covers everything the nightly job produces.

## Manifest schema

```json
{
  "generated_at": "2026-09-11T08:00:00Z",
  "last_backup_ok": "2026-09-10T02:30:30Z",
  "last_backup_failed": "",
  "failed_tenants": ["acme"],
  "backups": [
    {
      "id": "nightly-brk-2026-09-10",
      "tenant": "brk",
      "date": "2026-09-10T02:30:00Z",
      "source": "nightly",
      "db_bytes": 4027381,
      "fs_bytes": 565022,
      "has_filestore": true,
      "local_path": "/opt/backups/local/2026-09-10/brk.sql",
      "reached_b2": true,
      "restic_snapshot_id": "abc12345",
      "restic_repo": "daily"
    }
  ]
}
```

- `id` — stable natural key; the module upserts on it. Entries that disappear
  from the manifest (expired locally, pruned from B2) are removed from the
  registry on the next sync.
- `source` — `nightly` | `manual` | `pre_drop`.
- `reached_b2` / `restic_snapshot_id` / `restic_repo` come from the host-side
  `restic snapshots`; the container never runs restic.

## Module side

- `saas.backup._sync_backups()` reads the manifest and rebuilds the registry.
- `ir.cron` **SaaS: sync backup registry** runs it every 30 min; the Backups
  menu also refreshes on open.
- `saas.tenant.backup_state` (ok / stale / failed / never, stale = 48h) is
  derived from the registry plus `saas_backup.failed_tenants`.
