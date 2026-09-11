#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_bridge_surface.py
======================
Enumerate, per module, the exact symbols it consumes from Enterprise modules
that the bridge must supply (or that Phase 3 must neutralise).

For every absorbed/unemulated module it reports:
  * ``xmlid``          - ``module.identifier`` references (ref=, env.ref, ...)
  * ``model``          - ORM model names owned by that module
  * ``bundle``         - asset bundles owned by that module
  * ``static``         - ``module/static/...`` asset paths
  * ``pymodel``        - ``odoo.addons.<module>.models`` imports

Usage:
    python3 _toolkit/scan_bridge_surface.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import ModuleIndex, iter_text_files, read_text  # noqa: E402
from phase1_refactor import ABSORBED, UNEMULATED, Refactor  # noqa: E402

WATCH = sorted(ABSORBED | UNEMULATED)


def main() -> int:
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--source", default=os.path.normpath(os.path.join(here, "..")))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    source = os.path.abspath(args.source)
    root = os.path.dirname(os.path.dirname(source))
    roots = {
        "core": root + "/odoo/addons",
        "addons": root + "/addons",
        "enterprise": root + "/enterprise/addons",
        "enterprise_alt": root + "/custom/addions_enterprise",
        "source": source,
    }
    rf = Refactor(source, roots, apply=False, backup=False, mode="brand",
                  quarantine=False,
                  brand={"author": "x", "website": "x", "category": "x",
                         "license": "x", "icon_rel": "x"}, verbose=False)

    src_mods = rf.src_modules
    surface: dict = defaultdict(lambda: defaultdict(set))
    files: dict = defaultdict(lambda: defaultdict(set))

    for module in src_mods:
        text = rf._module_text(module)
        if not text:
            continue
        for token in WATCH:
            esc = re.escape(token)
            # xml-ids / dotted identifiers
            for m in re.finditer(rf"['\"]({esc}\.[A-Za-z_][\w]*)['\"]", text):
                surface[module]["xmlid"].add(m.group(1))
            for m in re.finditer(rf'=\s*["\']({esc}\.[A-Za-z_][\w]*)["\']', text):
                surface[module]["xmlid"].add(m.group(1))
            for m in re.finditer(rf"['\"]({esc})/static/[^'\"]*['\"]", text):
                surface[module]["static"].add(m.group(1))
            # models owned by the token
            for model in rf._models_of(token):
                if re.search(rf"['\"]{re.escape(model)}['\"]", text):
                    surface[module]["model"].add(model)

    # per-file detail for Phase 3
    for module in src_mods:
        mdir = os.path.join(source, module)
        for path in iter_text_files(mdir):
            txt = read_text(path) or ""
            rel = os.path.relpath(path, source)
            for token in WATCH:
                esc = re.escape(token)
                if (re.search(rf"['\"]{esc}\.[A-Za-z_]", txt)
                        or re.search(rf'=\s*["\']{esc}\.[A-Za-z_]', txt)):
                    for m in re.finditer(rf"['\"=]\s*['\"]?({esc}\.[A-Za-z_][\w]*)", txt):
                        files[module][rel].add(m.group(1))

    print("=" * 78)
    print("BRIDGE SURFACE — symbols consumed from Enterprise modules")
    print("=" * 78)
    totals = defaultdict(int)
    for module in sorted(surface):
        data = surface[module]
        if not any(data.values()):
            continue
        print(f"\n### {module}")
        for kind in ("model", "xmlid", "bundle", "static"):
            vals = sorted(data.get(kind, ()))
            if vals:
                totals[kind] += len(vals)
                print(f"  {kind:<8} ({len(vals)}): {', '.join(vals[:12])}"
                      + (" ..." if len(vals) > 12 else ""))
    print("\n=== totals ===")
    for k, v in sorted(totals.items()):
        print(f"  {k:<8} {v}")

    if args.json:
        payload = {
            "surface": {m: {k: sorted(v) for k, v in d.items()}
                        for m, d in surface.items()},
            "files": {m: {f: sorted(v) for f, v in d.items()}
                      for m, d in files.items()},
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        print(f"\n[json] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
