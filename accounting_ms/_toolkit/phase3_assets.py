#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase3_assets.py
================
Phase 3 of the msolutions accounting port: assets and view/template binding.

Actions
-------
1. **Bundle re-homing** - any manifest bundle whose owner module is not present
   on the Community path is re-homed into ``web.assets_backend``.
2. **Asset path pruning** - bundle entries pointing at ``<missing>/static/...``
   are dropped, since the file can never be resolved.
3. **Unresolved xml-id neutralisation** - data/view records referencing
   ``<missing>.<xmlid>`` are removed from the module's ``data``/``demo`` list
   (the file is quarantined with a ``.disabled`` suffix) so installation cannot
   fail with ``External ID not found``.
4. **OWL inheritance audit** - every ``t-inherit="M.x"`` target is checked
   against the available modules; missing targets are reported because they
   surface as browser-side template errors, not install errors.

Quarantined modules are skipped entirely: their files are never loaded.

Usage
-----
    python3 _toolkit/phase3_assets.py --dry-run
    python3 _toolkit/phase3_assets.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import (  # noqa: E402
    ENTERPRISE_BUNDLE_MAP, ModuleIndex, dump_manifest, iter_text_files,
    load_manifest, manifest_header, read_text, write_text,
)

TEMPLATE_INHERIT_RX = re.compile(r't-inherit="([A-Za-z_][\w]*\.[\w]+)"')
TEMPLATE_NAME_RX = re.compile(r't-name="([A-Za-z_][\w]*\.[\w]+)"')
ASSET_PATH_RX = re.compile(r"['\"]([A-Za-z_][\w]*)/static/")
XMLID_RX = re.compile(r'(?:ref|inherit_id|t-call|action|parent|groups)="([A-Za-z_][\w]*)\.[\w.]+"')
ENV_REF_RX = re.compile(r"env\.ref\(\s*['\"]([A-Za-z_][\w]*)\.[\w.]+['\"]")

# Community always provides these bundle namespaces.
CE_BUNDLE_PREFIXES = ("web.", "website.", "point_of_sale.", "mail.")


class Phase3:
    def __init__(self, source: str, roots: dict, apply: bool, verbose: bool = True):
        self.source = os.path.abspath(source)
        self.idx = ModuleIndex(roots)
        self.apply = apply
        self.verbose = verbose
        self.src_modules = sorted(n for n, i in self.idx.modules.items()
                                  if i.root_kind == "source")
        self.changes: list = []
        self.unresolved: dict = defaultdict(set)

    # ------------------------------------------------------------------
    def log(self, module: str, kind: str, detail: str) -> None:
        self.changes.append((module, kind, detail))
        if self.verbose:
            print(f"  [{kind:<16}] {module:<34} {detail}")

    def known(self, module: str) -> bool:
        return module in self.idx.modules

    def is_quarantined(self, module: str) -> bool:
        info = self.idx.modules.get(module)
        if not info or not info.manifest:
            return False
        return info.manifest.get("installable", True) is False

    # ------------------------------------------------------------------
    def fix_assets(self, module: str, manifest: dict) -> bool:
        assets = manifest.get("assets")
        if not isinstance(assets, dict):
            return False
        changed = False
        new_assets: dict = {}
        for bundle, files in assets.items():
            target = bundle
            owner = bundle.split(".")[0]
            if bundle in ENTERPRISE_BUNDLE_MAP:
                target = ENTERPRISE_BUNDLE_MAP[bundle]
            elif not self.known(owner) and not bundle.startswith(CE_BUNDLE_PREFIXES):
                target = "web.assets_backend"
            if target != bundle:
                self.log(module, "bundle", f"{bundle} -> {target}")
                changed = True
            bucket = new_assets.setdefault(target, [])
            if not isinstance(files, list):
                continue
            for entry in files:
                if isinstance(entry, (list, tuple)):
                    bucket.append(entry)
                    continue
                m = ASSET_PATH_RX.match(entry) or ASSET_PATH_RX.search(entry)
                if m and not self.known(m.group(1)):
                    self.log(module, "asset-drop", f"{entry} (missing owner {m.group(1)})")
                    changed = True
                    continue
                bucket.append(entry)
            # Merging two enterprise bundles into web.assets_backend can produce
            # duplicate entries (same file declared under both). Odoo would then
            # load the asset twice, so deduplicate while preserving order.
            seen = set()
            deduped = []
            for item in bucket:
                key = tuple(item) if isinstance(item, (list, tuple)) else item
                if key in seen:
                    self.log(module, "asset-dup", f"{item}")
                    changed = True
                    continue
                seen.add(key)
                deduped.append(item)
            new_assets[target] = deduped
        if changed:
            manifest["assets"] = new_assets
        return changed

    # ------------------------------------------------------------------
    def mark_disabled(self, path: str) -> None:
        """Rename a data file so Odoo never loads it, keeping it for reference."""
        if not self.apply:
            return
        if os.path.isfile(path) and not path.endswith(".disabled"):
            os.rename(path, path + ".disabled")

    def neutralise_unresolved(self, module: str, manifest: dict) -> bool:
        """Remove data/demo files that reference xml-ids of missing modules."""
        changed = False
        for key in ("data", "demo"):
            entries = manifest.get(key)
            if not isinstance(entries, list):
                continue
            kept = []
            for entry in entries:
                if not isinstance(entry, str) or not entry.endswith(".xml"):
                    kept.append(entry)
                    continue
                path = os.path.join(self.source, module, entry)
                text = read_text(path)
                if text is None:
                    kept.append(entry)
                    continue
                missing = set()
                for rx in (XMLID_RX, ENV_REF_RX):
                    for token in rx.findall(text):
                        if not self.known(token):
                            missing.add(token)
                if missing:
                    for token in missing:
                        self.unresolved[module].add(token)
                    if self.is_quarantined(module):
                        kept.append(entry)
                        continue
                    self.log(module, "neutralise",
                             f"{entry} (refs {sorted(missing)})")
                    self.mark_disabled(path)
                    changed = True
                    continue
                kept.append(entry)
            if changed:
                manifest[key] = kept
        return changed

    # ------------------------------------------------------------------
    def audit_owl_inheritance(self) -> None:
        targets = {}
        for module in self.src_modules:
            mdir = os.path.join(self.source, module)
            for path in iter_text_files(mdir):
                if not path.endswith(".xml"):
                    continue
                text = read_text(path) or ""
                if "<templates" not in text:
                    continue
                for name in TEMPLATE_NAME_RX.findall(text):
                    targets.setdefault(name, []).append(module)
        missing_targets = []
        for module in self.src_modules:
            if self.is_quarantined(module):
                continue
            for path in iter_text_files(os.path.join(self.source, module)):
                if not path.endswith(".xml"):
                    continue
                text = read_text(path) or ""
                for target in TEMPLATE_INHERIT_RX.findall(text):
                    owner = target.split(".")[0]
                    if target not in targets and not self.known(owner):
                        missing_targets.append((module, target))
        for module, target in missing_targets:
            self.log(module, "owl-missing", target)
        if not missing_targets:
            self.log("-", "owl-ok", "all t-inherit targets resolve to a bundled template")

    # ------------------------------------------------------------------
    def run(self) -> int:
        print(f"\n=== Phase 3 assets/views | apply={self.apply} ===")
        for module in self.src_modules:
            if self.is_quarantined(module):
                continue
            mpath = os.path.join(self.source, module, "__manifest__.py")
            src = read_text(mpath)
            if src is None:
                continue
            try:
                manifest = load_manifest(mpath)
            except Exception as exc:  # noqa: BLE001
                print(f"  [SKIP] {module}: {exc}")
                continue
            changed = self.fix_assets(module, manifest)
            changed = self.neutralise_unresolved(module, manifest) or changed
            if changed:
                new_src = dump_manifest(manifest, manifest_header(src))
                if self.apply:
                    write_text(mpath, new_src)
                else:
                    self.log(module, "dry-run", "manifest would be rewritten")
        self.audit_owl_inheritance()
        print("\n=== summary ===")
        kinds = defaultdict(int)
        for _m, kind, _d in self.changes:
            kinds[kind] += 1
        for kind, count in sorted(kinds.items()):
            print(f"  {kind:<18} {count}")
        if not self.apply:
            print("\n[dry-run] nothing written. Re-run with --apply.")
        return 0


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="msolutions Phase 3 assets/views")
    ap.add_argument("--source", default=os.path.normpath(os.path.join(here, "..")))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
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
    ph = Phase3(source, roots, apply=args.apply and not args.dry_run)
    rc = ph.run()
    if args.json:
        write_text(args.json, json.dumps({
            "changes": [{"module": m, "kind": k, "detail": d} for m, k, d in ph.changes],
            "unresolved": {m: sorted(v) for m, v in ph.unresolved.items()},
        }, indent=2))
        print(f"[json] {args.json}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
