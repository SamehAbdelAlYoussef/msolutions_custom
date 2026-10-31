# Section 4 — Restore: design (for approval, no code yet)

## The one scenario everything follows from

The failure that matters is **not** "restore failed." It is **"restore failed
halfway"**: the old data is already gone, the new data never landed, the customer
lost everything because we tried to help.

**The governing invariant of this design:**

> **Nothing is ever DELETED during a restore.** The current state is *renamed and
> kept*, never dropped. The restored copy is built *beside* the live tenant, never
> over it. The old copy is dropped only much later, by a separate deferred cleanup,
> after the restore has demonstrably succeeded — and even then only after a
> retention window.

If a step dies, every byte of both the old and the new state still exists on disk
under a known name. Recovery is always "finish the rename" or "rename back" —
never "recover from a backup we hope exists." Because of this invariant, **no row
of the failure table (§j) ends in data loss.**

---

## Architecture (reuses what is already built and proven)

| Concern | Mechanism | Reused from |
|---|---|---|
| Assemble the chosen backup (dump.sql + filestore) from local disk or B2 | host runner `backup_restore.sh` restores into a staging dir | **Section 3** (already built + tested: local <7 days, else `restic restore`) |
| Safety backup of current state, abort on failure | `_dump_tenant` (the single dump path) | **Section 2 / pre-drop** (`_backup_before_drop` pattern) |
| Create/drop/rename DB, grant ownership, copy/rename filestore | raw psycopg via `odoo.sql_db.db_connect()` + `subprocess` (`psql` to load the dump); **never a tenant Registry** | provisioning/drop code |
| Global mutual exclusion with nightly + Backup Now + Download | the **same `flock`** on the shared-inode filestore lock | Section 2 |
| State machine + journal + watchdog | new `saas.restore.job` model (mirrors `saas.backup.job`) | Section 2 |
| Integrity check of the chosen backup | dump-size sanity + filestore-count vs `ir_attachment` | Section 3 |

**New model `saas.restore.job`** is the *journal*. Every stage transition is
committed to it, so a watchdog (and a human) can read exactly where a dead restore
stopped and reconcile it deterministically. It records: tenant, backup ref + date,
reason, requester, `scratch_db`, `old_db`, `old_fs`, `safety_backup_ref`, and the
per-stage timestamps.

**Naming used throughout** (tenant `noveen`, timestamp `TS`):
- live: DB `noveen`, filestore `filestore/noveen`
- scratch: DB `noveen__restore_TS`, filestore `filestore/noveen__restore_TS`
- old (after swap): DB `noveen__old_TS`, filestore `filestore/noveen__old_TS`

---

## Lifecycle (stages)

1. **Pre-flight** — dev-group gate, control-plane assert, the refusals (§k), disk
   pre-flight, acquire the flock (non-blocking; if held, stay queued), integrity
   check of the chosen backup. *Live tenant untouched.*
2. **Safety backup** — `_dump_tenant` of the CURRENT state → local + registered +
   swept to B2. **Abort the whole restore if this fails** (§a). *Live untouched.*
3. **Assemble** — `backup_restore.sh` puts the chosen backup's `dump.sql` +
   `filestore/` into a staging dir (local copy if <7 days, else restic from B2).
   *Live untouched.*
4. **Build scratch** — `CREATE DATABASE noveen__restore_TS OWNER odoo_provision`;
   load `dump.sql` with `psql`; **re-apply the ownership split** (§d); copy the
   staged filestore into `filestore/noveen__restore_TS`. *Live untouched — a
   separate database and directory.*
5. **Verify scratch** (§b) — see checks below. Pass → continue; fail → drop the
   scratch, abort. *Live untouched.*
6. **Enter maintenance** (§g) — flag the tenant `restoring`, force the routing
   sync, confirm nginx is serving the maintenance page for it.
7. **Terminate connections** to the live DB.
8. **Swap the database** — two `ALTER DATABASE … RENAME` (see §c walkthrough).
9. **Swap the filestore** — two `os.rename` (atomic, same filesystem).
10. **Finalize** — invalidate the registry, rotate `database.secret` (logs
    sessions out), handle `database.uuid` (§f).
11. **Exit maintenance** — clear the flag, sync routing, tenant back online on the
    restored data.
12. **Post** — audit + chatter (§i); keep `noveen__old_TS` (DB + fs) and the safety
    backup for a retention window; a separate deferred cleanup drops the old copy
    later. Release the flock.

**Verify-scratch checks (step 5), all raw psycopg on the scratch DB:**
- `psql` load exited 0.
- Core tables present and non-empty: `ir_module_module`, `res_users`, `res_company`,
  `ir_attachment`.
- **Ownership assertion** (§d): every object in `public` is owned by `odoo_web`,
  the database by `odoo_provision`. *This is checked BEFORE the swap so the
  silent-ownership break can never reach a live tenant.*
- DB+filestore consistency: `COUNT(DISTINCT store_fname)` in `ir_attachment` ≤ files
  staged in `filestore/` (same gate as Download).
- `database.uuid` present.
- `pg_database_size` is plausibly ≥ the recorded backup size.

---

## Answers to your required questions

### a. Safety backup first
Stage 2 dumps the **current** state via `_dump_tenant` and **aborts the restore if
that dump fails or is implausibly small** — the exact `_backup_before_drop`
contract that has already refused three times. The safety backup is registered and
swept to B2, so the pre-restore state is recoverable off-host, independent of the
on-disk `__old_TS` copy.
*Variant:* if the tenant currently has **no** database (you're restoring after an
accidental drop), there is nothing live to back up — stage 2 is skipped, and the
pre-drop backup that the drop already took is the "before" copy.

### b. Scratch first, always
Non-negotiable and built in: stages 4–5 restore into `noveen__restore_TS` and
verify it **before** the swap even begins. A backup that won't load, or is
inconsistent, or comes out with wrong ownership, is caught here and the restore
aborts with the **live tenant never touched**.

### c. The swap — rename, not drop-and-recreate

**Decision: RENAME.** Drop-and-recreate is disqualified by the governing invariant:
if the process dies after the drop and before the recreate, the tenant has **no
database and its data is gone**. Rename never destroys, so there is no instant at
which data does not exist somewhere on disk.

The swap runs inside the maintenance window, after connections are terminated. What
exists on disk **at each instant**:

| Instant | Databases on disk | Filestores on disk | Tenant name resolves? |
|---|---|---|---|
| t0 (before) | `noveen` (live), `noveen__restore_TS` (verified) | `noveen`, `noveen__restore_TS` | yes → old |
| t1 `ALTER DATABASE noveen RENAME TO noveen__old_TS` | `noveen__old_TS`, `noveen__restore_TS` | `noveen`, `noveen__restore_TS` | **no DB for name** (ms) |
| t2 `ALTER DATABASE noveen__restore_TS RENAME TO noveen` | `noveen` (restored), `noveen__old_TS` | `noveen`, `noveen__restore_TS` | yes → restored (DB) |
| t3 `os.rename filestore/noveen → filestore/noveen__old_TS` | `noveen`, `noveen__old_TS` | `noveen__old_TS`, `noveen__restore_TS` | DB yes, fs missing |
| t4 `os.rename filestore/noveen__restore_TS → filestore/noveen` | `noveen`, `noveen__old_TS` | `noveen`, `noveen__old_TS` | yes → restored (DB+fs) |

At **every** instant both the old copy (as `noveen` or `noveen__old_TS`) and the
restored copy (as `noveen__restore_TS` or `noveen`) exist in full. The tenant is in
**maintenance for the whole of t1→t4**, so the brief "no DB for the name" and "fs
missing" states are never served to a user, and a new-DB/old-filestore mismatch is
never served either.

**If the process dies mid-swap:** the journal says which renames completed; the
watchdog **rolls back** by reversing the completed renames (old copy is always
present), returns the tenant to exactly its pre-restore state, marks the job
`failed`, and you retry. Roll-back (not roll-forward) is the conservative choice:
you get back precisely what you had. The scratch remains for the retry.

`ALTER DATABASE … RENAME` cannot run inside a transaction and requires no
connections — hence stage 7. The two renames are sequential, not one atomic
statement; the invariant tolerates that because neither rename deletes.

### d. Ownership
The dumps are `--no-owner`, so objects load owned by **whoever runs `psql`**
(`odoo_provision`) — ownership is **not** inherited from the backup and **must be
reapplied explicitly**. On the scratch DB, after the load, we run the proven
`_GRANT_WEB_OWNERSHIP_SQL` (schema objects → `odoo_web`) and `GRANT CONNECT … TO
odoo_web`; the database itself stays owned by `odoo_provision` (it created it).
Because this is the thing that "won't surface until pg_dump fails weeks later,"
**verification (step 5) asserts the ownership split is correct before the swap** —
a wrong split fails verification and never reaches a live tenant.

### e. Filestore — atomic with the database
The filestore is restored into the scratch alongside the DB (stage 4) and swapped
in the **same maintenance window** as the DB (t3–t4). The tenant is not brought
back online until **both** the DB and its filestore are the restored ones. So:
- **filestore fails during stage 4** → abort before the swap; live untouched.
- **filestore rename fails during the swap (t3/t4)** → tenant is still in
  maintenance (never served the mismatch); the watchdog rolls back the DB rename
  too, tenant returns to old DB **and** old filestore together. A DB-without-its-
  filestore is never served and never left as the resting state.

### f. Sessions, cache, UUID
- **`database.uuid`:** kept as the backup's uuid. This is a *restore of the same
  tenant to its own earlier state*, not a copy, so the identity is unchanged and we
  do **not** run Odoo's neutralization.
- **Registry:** after the swap, `Registry.delete(tenant)` + close its connections
  so the next request loads the fresh database; Odoo's registry signaling
  propagates the reload across web workers. (This is invalidation, not opening a
  tenant registry.)
- **Sessions:** we rotate `database.secret` in the restored DB, which invalidates
  every existing session signature → all users are logged out and must
  re-authenticate. (Filestore sessions are global and not per-tenant-filterable;
  rotating the secret is the deterministic way to end them.)
- **Users logged in during the restore:** during the swap seconds they get the
  **maintenance page** (§g); afterward their session is invalid → the login page.
  Unsaved in-browser work is lost — inherent to any restore.

### g. Downtime
Only the **swap** takes the tenant offline (seconds); stages 2–5 (safety backup,
assemble, build scratch, verify — the slow parts) run with the tenant **fully
live**. The tenant is offlined by setting a `restoring` flag that
`update_tenant_list.sh` emits into a new `maintenance.conf` map; nginx serves a
**503 "under maintenance" page** for maintenance-listed hosts. The restore sets the
flag, **forces the routing sync and confirms the page is live before touching
anything**, does the swap, then clears the flag and syncs again.
A user hitting the tenant mid-swap sees the **maintenance page, not a database
error**. *(This is the one piece needing a small `nginx.conf` change — a
`$maintenance_tenant` map + a 503 location + the page — edited in place per the
single-file bind-mount inode trap.)*

### h. Confirmation
Not "Are you sure?". The screen requires the **tenant name typed by hand** and a
**written reason**, and states exactly what will happen, with **both dates and both
sizes side by side**:

```
RESTORE noveen
  Current data  →  becomes the safety backup, then kept 7 days as noveen__old_TS
      as of  2026-09-11 14:20   ·   size  512 MB DB + 88 MB files
  Restoring the backup:
      as of  2026-08-21 02:30   ·   size  460 MB DB + 80 MB files   ← 3 WEEKS OLDER
  Type the tenant name to confirm:  [ __________ ]
  Reason:                           [ __________ ]
```
The single most valuable line is **the backup's date next to the current data's
date**, with the age gap called out, so three-week-old data can't be restored in
the belief it is yesterday's.

### i. Audit
A permanent audit record (new actions `restore_queued` / `restore_ok` /
`restore_failed`) capturing who, when, which backup (ref + date), the reason, the
safety-backup reference, and the outcome — **plus a chatter entry on the tenant**
(`message_post`). This is the most destructive action available; the trail is
immutable (the audit log already blocks write/unlink).

### j. Failure at every stage — no row ends in data loss

| # | Stage | If it fails here, the tenant is… | Recovery |
|---|---|---|---|
| 1 | Pre-flight (gate, refusals, disk, lock, integrity) | **live, untouched** | fix the cause (disk / lock / bad backup) and retry; nothing happened |
| 2 | Safety backup of current state | **live, untouched** | restore **refused** because we couldn't protect the current state (the `_backup_before_drop` contract) |
| 3 | Assemble chosen backup (local/B2) | **live, untouched**; safety backup already exists | retry, or pick another backup; live never touched |
| 4 | Build scratch (create/load/own/filestore) | **live, untouched**; scratch dropped | retry; the scratch is a separate DB+dir, live is not involved |
| 5 | Verify scratch (incl. ownership + fs-consistency) | **live, untouched**; scratch dropped | the backup is bad → choose another; live never touched |
| 6 | Enter maintenance | **live** (maintenance lifted on failure) | retry |
| 7 | Terminate connections | **live** (maintenance lifted) | retry |
| 8 | Swap DB (t1–t2) — dies mid-rename | name may momentarily have no DB, **but `__old_TS` + scratch both exist** | watchdog reverses completed renames → back to old; nothing lost |
| 9 | Swap filestore (t3–t4) — dies mid-rename | still in maintenance; mismatch never served; **old fs + scratch fs both exist** | watchdog reverses DB+fs renames → old DB+fs together; nothing lost |
| 10 | Finalize (registry / secret / uuid) | **live on restored data** -- the swap already committed (point of no return passed) | finalize is best-effort: each step is caught and logged, the job still lands on `done`, and `_advance` never overturns a completed swap; worst case a stale session lingers until it expires; data intact |
| 11 | Exit maintenance | **restored but offline** | remove from the maintenance map by hand; data intact |
| — | Process dies at any instant | determined by the journal + which DBs/dirs exist | watchdog reconciles deterministically: every state is "finish the rename" or "rename back" — **never "restore from a backup"** |
| — | After success, you regret it | live on restored data | `noveen__old_TS` (DB+fs) is kept 7 days → rename it back; and the safety backup is in B2 |

There is no row in which the tenant is broken and the data is lost, because at no
instant does the data fail to exist somewhere on disk.

### k. What it refuses to do
- **Cross-tenant restore** — the backup's tenant name must equal the target's.
- **Tenant mid-transition** — refuse if state is `provisioning`/`terminating`, or
  if a backup job or another restore for this tenant is active. (Restoring onto an
  `active` tenant, or onto one whose DB is gone after a drop, is allowed — that's
  the point.)
- **Backup that fails its integrity check** (dump size / filestore count / restic
  checksum on restore).
- **Restore while the flock is held** by the nightly, a Backup Now, a Download, or
  another restore — it waits/queues, it never forces.
- **Restore when disk pre-flight fails** (below).

---

## Also

- **Shared flock:** the restore takes the **same** `flock` as backup/download for
  the whole operation, so it can never run while that tenant (or any) is being
  dumped. One heavy operation at a time, globally.
- **Disk pre-flight:** a restore needs room, *simultaneously*, for the **safety
  backup** (dump + tar of current ≈ `current_db + current_fs`), the **loaded
  scratch database** (a restored DB is larger than its plain dump once indexes are
  rebuilt — budget ≈ **3 × the backup's dump-db bytes**), the **scratch filestore**
  (≈ backup fs bytes), and the **staging copy** (≈ backup db + fs). Proposed
  requirement, free on the filestore volume:
  `(current_db + current_fs) + 3×(backup_db_dump) + 2×(backup_fs) + 5 GB floor`.
  If short, refuse with the exact numbers, like Backup Now. *(The 3× is the one
  soft estimate; I'll size it against a real restore during implementation.)*
- **Developer group only.**
- **Never open a tenant Registry** — all DB work is raw psycopg via
  `db_connect()` inside `closing()`, plus `psql`/`_dump_tenant` subprocesses; the
  only Registry call is `Registry.delete` to *invalidate* after the swap.

---

## Open questions for you before I write code

1. **Roll-back vs roll-forward on mid-swap death.** I chose **roll back to the
   pre-restore state** (conservative — you get exactly what you had, then retry).
   The alternative is roll-forward (the scratch was already verified). Your call.
2. **Old-copy retention.** I proposed keeping `__old_TS` (DB + filestore) for **7
   days** before the deferred cleanup drops it. Longer / shorter?
3. **Maintenance page** requires the one `nginx.conf` edit (`$maintenance_tenant`
   map + 503 page). OK to make that infra change, or would you rather the swap
   window just return the tenant's normal Odoo error for those few seconds (no
   nginx change, uglier)?
4. **Safety backup to B2 timing.** Sweep it with the nightly (simplest) or force an
   immediate B2 upload before the swap (off-host guarantee, at the cost of coupling
   to the Backup Now uploader)?

---

## Approved decisions + three additions (post-review)

**Decisions:** roll **back** on mid-swap death · retain the old copy **7 days** ·
**yes** to the nginx maintenance page · safety backup is an **immediate B2 upload**
(reuse the Backup Now job, wait for `reached_b2` before proceeding, abort if it
fails).

### 1. The deferred cleanup is the only code that deletes — treat it as such
The 7-day cleanup must **verify, at the moment of deletion**, before dropping
`noveen__old_TS`:
- the **live tenant of that name exists, resolves over HTTP (200), and has a
  working database** (raw psycopg connect + a trivial query);
- the **safety backup for that restore reached B2** (via the restore job's linked
  backup record).
If **either** check fails: **leave the old copy untouched and raise an alert.**
Never delete on a timer alone.
Retained copies consume **full disk for 7 days** → counted in the restore disk
pre-flight, and surfaced on the dashboard: **"N retained pre-restore copies, X GB"**.

### 2. The confirmation screen protects against the operator, not the system
Side by side: current data (timestamp + size), backup (timestamp + size), and the
age gap in **plain words** — *"You are about to discard 23 days of data."* When the
gap is large, that line is made impossible to miss (full-width, red). The likeliest
mistake is restoring the wrong backup while tired at 2am, so this line is the real
safety feature.

### 3. Dry run — the common case, and it never risks the live tenant
A mode that runs everything **except the swap**: restore to scratch, verify it
loads, check ownership + filestore, and **report record counts from both copies
side by side**, then **stop and leave the scratch DB for inspection**. Answers "is
this backup good?" without touching production. Scratch left is cleaned by the same
retention/watchdog machinery (with its own, shorter TTL).
