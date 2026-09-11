#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze.py
==========
Read-only reconnaissance of the accounting_ms port surface.

Outputs
-------
* Dependency status of every module (CE-resolvable vs enterprise-only vs missing).
* Rename-impact: how many inbound references each module name receives.
* Asset bundles that only Enterprise provides.
* Cross-module XML-id references into modules that will not be loaded.

Run:
    python3 _toolkit/analyze.py [--source DIR] [--json OUT]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import (  # noqa: E402
    ModuleIndex, Report, detect_workspace, iter_text_files, read_text, scan_xrefs,
)

# Modules that are pure-Community; anything outside this set cannot be assumed
# present once the port targets a stock Community addons_path.
CE_KINDS = {"core", "addons", "source"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    roots = detect_workspace(args.source)
    idx = ModuleIndex(roots)
    source = os.path.abspath(args.source)

    rep = Report("accounting_ms PORT ANALYSIS")

    src_mods = sorted(n for n, i in idx.modules.items() if i.root_kind == "source")
    rep.add("0. inventory", f"source modules        : {len(src_mods)}")
    for kind in ("core", "addons", "enterprise", "enterprise_alt"):
        cnt = sum(1 for i in idx.modules.values() if i.root_kind == kind)
        rep.add("0. inventory", f"{kind:<21}: {cnt} modules indexed")
    rep.add("0. inventory", f"roots                 : {roots}")

    # ---- dependency status -------------------------------------------------
    dep_status = defaultdict(set)      # dep -> statuses
    per_module = defaultdict(list)
    absent = defaultdict(set)          # dep -> modules needing it
    for name in src_mods:
        info = idx.modules[name]
        if info.manifest_error:
            rep.add("1. manifest errors", f"{name}: {info.manifest_error}")
        for dep in info.depends:
            if dep in src_mods:
                status = "source"
            else:
                status = idx.classify(dep)
            dep_status[dep].add(status)
            per_module[name].append((dep, status))
            if status not in CE_KINDS:
                absent[dep].add(name)

    rep.add("1. depends needing bridge/stub (not CE-resolvable)",
            "dep | required by N modules | available in")
    for dep in sorted(absent):
        statuses = sorted(s for s in dep_status[dep] if s != "source")
        rep.add("1. depends needing bridge/stub (not CE-resolvable)",
                f"{dep:<34} x{len(absent[dep]):<3} {','.join(statuses) or 'MISSING'}")

    rep.add("1b. deps resolved by Enterprise path only",
            "dep | N modules (must be replaced/absorbed for a pure-CE run)")
    for dep in sorted(dep_status):
        st = dep_status[dep]
        if st <= {"enterprise", "enterprise_alt"}:
            users = [m for m in src_mods if (dep, next(iter(st))) in per_module[m]]
            rep.add("1b. deps resolved by Enterprise path only",
                    f"{dep:<34} x{len(users):<3} -> {','.join(sorted(st))}")

    # ---- rename impact -----------------------------------------------------
    inbound = Counter()
    kinds = defaultdict(Counter)
    for hit in scan_xrefs(source):
        if hit.module in idx.modules:
            inbound[hit.module] += 1
            kinds[hit.module][hit.kind] += 1
    rep.add("2. rename impact (inbound refs from OTHER modules)",
            "module | external_refs | kinds | SAFE TO RENAME?")
    safe, risky = [], []
    for name in src_mods:
        n = inbound.get(name, 0)
        kk = ",".join(f"{k}:{v}" for k, v in kinds[name].most_common(4))
        verdict = "SAFE" if n == 0 else "REWRITE-REQUIRED"
        (safe if n == 0 else risky).append(name)
        rep.add("2. rename impact (inbound refs from OTHER modules)",
                f"{name:<34} {n:<5} {kk[:56]:<56} {verdict}")
    rep.add("2b. rename verdict",
            f"safe to rename directly: {len(safe)} -> {safe}")
    rep.add("2b. rename verdict",
            f"need reference rewrite : {len(risky)}")

    # ---- asset bundles -----------------------------------------------------
    bundles = Counter()
    for name in src_mods:
        assets = (idx.modules[name].manifest or {}).get("assets") or {}
        for bundle in assets:
            bundles[bundle] += 1
    rep.add("3. asset bundles used by source modules",
            "bundle | #modules")
    for bundle, cnt in bundles.most_common():
        flag = ""
        if not bundle.startswith("web.") and bundle.split(".")[0] not in idx.modules:
            flag = "  <-- BUNDLE OWNER MISSING"
        elif bundle.split(".")[0] not in idx.modules and bundle.split(".")[0] not in ("web",):
            flag = "  <-- external bundle"
        rep.add("3. asset bundles used by source modules", f"{bundle:<52} {cnt}{flag}")

    # ---- xrefs into absent modules ----------------------------------------
    xr = Counter()
    for hit in scan_xrefs(source):
        if hit.module not in idx.modules:
            xr[hit.token] += 1
        elif idx.modules[hit.module].root_kind not in CE_KINDS and hit.module not in src_mods:
            xr[hit.token] += 1
    rep.add("4. unresolved external xml-ids / assets", "token | count")
    for token, cnt in xr.most_common(80):
        rep.add("4. unresolved external xml-ids / assets", f"{token:<60} {cnt}")

    rep.render()
    if args.json:
        rep.to_json(args.json)
        print(f"[json] written -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
