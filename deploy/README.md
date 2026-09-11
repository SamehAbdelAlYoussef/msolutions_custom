# deploy/ — msolutions production ops scripts

These are the **live production scripts** that run the SaaS host: the nightly
backup, the offsite retention prune, the backup-registry manifest generator, the
nginx tenant-routing sync, and the plan-template rebuild.

They live here because if the box is lost the Odoo module can be restored from
GitHub in seconds — but these scripts existed **nowhere else**. The script that
protects everything was the least-protected thing on the server. Not any more.

---

## The authoritative copy is the one on the server

For every script here, the **live copy under `/opt/scripts/` on the Odoo host is
authoritative**. The server runs that copy, not this one. This directory is a
version-controlled mirror for disaster recovery and review.

### THE RULE (this is what burned us once already)

> **Any change made to a live `/opt/scripts/*` script must be mirrored into this
> `deploy/` directory the _same day_, in a commit.**

A fix that lives only on the server is a fix that disappears when the server
does. Same day. Every time.

---

## Two kinds of copy

**Mirrored — kept byte-identical to live** (edit one → copy to the other):

| script | live path | what it does |
|---|---|---|
| `backup_manifest.sh`    | `/opt/scripts/backup_manifest.sh`    | host-side, read-only generator of the backup-registry JSON the Odoo module reads (runs every 15 min via `odoo-backup-manifest.timer`) |
| `update_tenant_list.sh` | `/opt/scripts/update_tenant_list.sh` | regenerates the nginx tenant + suspended maps (runs ~every 2 s via `odoo-tenant-sync.timer`) |
| `restic_prune.sh`       | `/opt/scripts/restic_prune.sh`       | weekly offsite space reclamation (`odoo-prune.timer`) |
| `rebuild_templates.sh`  | `/opt/scripts/rebuild_templates.sh`  | rebuilds all SaaS plan templates (run manually after an upgrade) |

**Reference only — deliberately NOT identical to live:**

| script | live path | why it differs |
|---|---|---|
| `backup_tenants.sh` | `/opt/scripts/backup_tenants.sh` | The nightly backup (`odoo-backup.timer`, 04:30 UTC). The **entire backup chain depends on it**, so the tested live copy is never replaced. The live copy keeps a couple of hardcoded infrastructure defaults for resilience; this repo copy reads them from `backup.env` instead. **Do not deploy this copy over the live one.** Mirror logic changes both ways by hand. |

`backup_status.sh` (the login MOTD freshness indicator) is intentionally **not**
mirrored here: scrubbing its one path cleanly would mean sourcing the root-only
`backup.env` from an unprivileged MOTD context, which risks breaking login MOTD
for a path that is no secret. It is cosmetic and trivially reconstructable.

---

## No secrets in this directory

Every infrastructure value — DB host, the backup paths, the restic repository
URL, bucket, and all credentials — is read at runtime from server-only env files
that are **not** in this repo:

- `/opt/scripts/backup.env`  (mode `600`, root) — backup DB creds, paths, restic repos + keys, alert tokens
- `/opt/scripts/deploy.env`  (mode `600`, root) — control DB, base domain, container + nginx-conf paths

In the committed scripts these stay **variable references** (`"$RESTIC_REPOSITORY"`,
`"${DB_HOST:?…}"`), never values. If you add a new infrastructure value, put it in
the appropriate `.env` on the server and reference it by name here — never inline it.

---

## Drift check

`check_drift.sh` compares each live script against its `deploy/` copy:

```bash
sudo /opt/odoo/msolutions_custom/deploy/check_drift.sh
```

- **Mirrored scripts** → exact `diff`; any difference is drift.
- **`backup_tenants.sh`** → the two are normalized so the scrubbed infra lines
  collapse to the same form, then the **executable logic** is compared. A clean
  result means the logic matches; a warning means real drift a human must
  reconcile. Best-effort by design — treat a warning as "look", not "panic".

Run it after any ops-script change, and periodically (e.g. from cron) if you want
an early warning that the server and the repo have drifted apart.
