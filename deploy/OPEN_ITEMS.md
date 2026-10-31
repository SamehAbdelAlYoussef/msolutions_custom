# msolutions_saas — open items

Tracked issues found but deliberately NOT fixed yet, with why.

## 1. Missing plan templates cause SILENT slow provisioning (PRODUCTION BUG)

**Severity:** production — breaks the signup promise for the highest-paying plans.

`saas_plan` rows reference template databases that do not exist on the server:

| Plan | `template_db` | Template DB present? | Provisioning path |
|------|---------------|----------------------|-------------------|
| Basic | `tpl_basic` | **yes** | template clone — **seconds** |
| Business | `tpl_business` | **no** | falls back to from-scratch — **minutes** |
| Advanced | `tpl_advanced` | **no** | falls back to from-scratch — **minutes** |

`saas_tenant._template_db()` returns `None` when the named template is missing
and only logs a WARNING; `_provision_database()` then builds from scratch. So a
Business/Advanced signup silently takes minutes instead of seconds, with **no
error surfaced to anyone** — nobody notices until a customer complains. Silent
degradation is worse than a loud failure.

**To fix (AFTER the load test):**
- Build `tpl_business` / `tpl_advanced` (or point those plans at a real
  template), so the paid plans clone fast like Basic.
- **Surface it on the dashboard**: if any active plan's `template_db` is set but
  the template DB is missing, show a visible warning on the SaaS dashboard
  (health strip) — not just a log line. A plan that will provision slowly must
  be visible before a customer hits it.

**Measured cost (load test):** idle from-scratch build ≈ TBD; under N concurrent
provisions ≈ TBD. (Recorded during the load test — the real cost of the gap.)

## 2. Load-test capacity finding (2026-09-15) — plan onboarding against MATURE tenants

Measured on the DB host (75 GB NVMe, PostgreSQL on a separate box from Odoo) by
cloning a validated **427 MB mature tenant** (mail 57%, real account/mrp volume)
through the real provisioning path, in stages 10→25→50→80→100, watchdog at
40 GB PG-data / 400 conns / 2 GB app-RAM.

**DISK is the sole binding constraint. Everything else stayed flat to 100 tenants:**
noveen p95 115–265 ms throughout, cache hit ≥ 99.93%, RAM 21.4→19.1 GB avail,
connections ≤ 70/500, 0 OOM, clone-provisioning 3.4–3.9 s/tenant (no degradation
under load). PG-data grows dead-linear at ~0.43 GB per mature tenant.

- **COMFORTABLE ≈ 70 mature tenants (~30 GB).**
- **STRAINED ≈ 85 (~37 GB)** — no perf degradation, just near the ceiling.
- **BROKEN / stop ≈ 93–95 (40 GB watchdog abort).** Kept serving to 100/42 GB.

**EARLIEST WARNING SIGN = PG-data itself; alert at 30 GB (75% of ceiling ≈ 70
tenants).** This failure mode is a CLIFF, not a slope — no latency/RAM/cache
signal moves in time to warn; disk-full stops PostgreSQL outright (unrecoverable
without free space). Do NOT rely on performance metrics for early warning.

Capacity shrinks ~4× as customers mature (~70 mature vs ~300+ young @ 90 MB).

### Gaps found during the test (fix later)
- **Watchdog abort flag is host-side (`/opt/scripts/.loadtest_ABORT`); the
  in-container provisioner can't see it** → it overshot 95→100 before the
  host-side driver stopped it. Safe here (35 GB reserve), but an in-band stop
  needs a container-visible signal (a DB flag row, or a flag under the filestore
  the container can read).
- **From-scratch provisioning under load is unquantified** — the test measured
  *clone* provisioning (fast). The missing-template path (item 1) is minutes per
  tenant and should be measured separately before relying on Business/Advanced.
