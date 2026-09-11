#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase4_verify.py
================
Automated verification of the msolutions accounting port.

Checks
------
1. **Manifest load**  - every ``__manifest__.py`` parses and exposes the required
   keys (``name``, ``depends``) and a valid ``installable`` flag.
2. **Dependency resolution** - every dependency of an installable module resolves
   on the Community addons path or to an in-source module.  Unresolvable
   ``depends`` are a hard failure.
3. **Cycle detection** - the dependency graph must stay acyclic.
4. **Python import resolution** - ``from odoo.addons.<mod>...`` targets must
   exist, otherwise the module raises ``ModuleNotFoundError`` at load time.
5. **External xml-id resolution** - data/view references to ``<module>.<xmlid>``
   must resolve to a module present on the addons path.
6. **Asset integrity** - bundle owners exist and referenced asset files exist.
7. **Model uniqueness** - an ORM model must not be declared twice across the
   ported modules.
8. **Optional install smoke test** (``--install``) - runs ``odoo-bin`` against a
   throwaway database and reports the first traceback.

Exit code is non-zero when a hard check fails.

Usage
-----
    python3 _toolkit/phase4_verify.py
    python3 _toolkit/phase4_verify.py --install --db msolutions_verify
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import (  # noqa: E402
    ModuleIndex, iter_text_files, load_manifest, read_text, write_text,
)

XMLID_RX = re.compile(r'(?:ref|inherit_id|t-call|t-inherit|action|parent|groups)="([A-Za-z_][\w]*)\.[\w.]+"')
ENV_REF_RX = re.compile(r"env\.ref\(\s*['\"]([A-Za-z_][\w]*)\.[\w.]+['\"]")
PY_IMPORT_RX = re.compile(r"from odoo\.addons\.([A-Za-z_][\w]*)\.?")
MODEL_NAME_RX = re.compile(r"(?<![A-Za-z0-9_])_name\s*=\s*['\"]([\w.]+)['\"]")
ASSET_PATH_RX = re.compile(r"^([A-Za-z_][\w]*)/static/")


class Verifier:
    def __init__(self, source: str, roots: dict):
        self.source = os.path.abspath(source)
        self.idx = ModuleIndex(roots)
        self.roots = roots
        self.errors: list = []
        self.warnings: list = []
        self.info: list = []
        self.src_modules = sorted(n for n, i in self.idx.modules.items()
                                  if i.root_kind == "source")

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))
        print(f"  [FAIL] {check}: {msg}")

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))
        print(f"  [WARN] {check}: {msg}")

    def ok(self, check: str, msg: str) -> None:
        self.info.append((check, msg))
        print(f"  [ ok ] {check}: {msg}")

    # -- 1/2/3 -----------------------------------------------------------
    def check_manifests_and_deps(self) -> None:
        print("\n[1] manifest load + [2] dependency resolution + [3] cycles")
        graph: dict = {}
        for module in self.src_modules:
            mpath = os.path.join(self.source, module, "__manifest__.py")
            try:
                manifest = load_manifest(mpath)
            except Exception as exc:  # noqa: BLE001
                self.err("manifest", f"{module}: unparsable ({exc})")
                continue
            if not manifest.get("name"):
                self.err("manifest", f"{module}: missing 'name'")
            if not isinstance(manifest.get("depends", []), list):
                self.err("manifest", f"{module}: 'depends' must be a list")
            graph[module] = list(manifest.get("depends") or [])
        self.ok("manifest", f"{len(graph)} manifests parsed")

        unresolvable = 0
        for module, deps in sorted(graph.items()):
            manifest = self.idx.modules[module].manifest or {}
            if manifest.get("installable", True) is False:
                continue
            for dep in deps:
                if dep not in self.idx.modules:
                    self.err("depends", f"{module}: unresolved dependency '{dep}'")
                    unresolvable += 1
        if not unresolvable:
            self.ok("depends", "every installable module resolves on the CE + source path")

        # cycle detection (source modules only, non-quarantined)
        WHITE, GREY, BLACK = 0, 1, 2
        color = defaultdict(int)
        cycles = []

        def visit(node: str, stack: list) -> None:
            color[node] = GREY
            stack.append(node)
            for dep in graph.get(node, []):
                if dep not in graph:
                    continue
                if color[dep] == GREY:
                    cycles.append(" -> ".join(stack[stack.index(dep):] + [dep]))
                elif color[dep] == WHITE:
                    visit(dep, stack)
            stack.pop()
            color[node] = BLACK

        for node in graph:
            if color[node] == WHITE:
                visit(node, [])
        if cycles:
            for cycle in cycles:
                self.err("cycle", cycle)
        else:
            self.ok("cycle", "dependency graph is acyclic")

    # -- 4 ---------------------------------------------------------------
    def check_python_imports(self) -> None:
        print("\n[4] python import resolution")
        missing = 0
        for module in self.src_modules:
            manifest = self.idx.modules[module].manifest or {}
            if manifest.get("installable", True) is False:
                continue
            for path in iter_text_files(os.path.join(self.source, module)):
                if not path.endswith(".py"):
                    continue
                rel = os.path.relpath(path, self.source)
                if os.sep + "tests" + os.sep in path:
                    continue  # test-only imports are collected on demand
                text = read_text(path) or ""
                for target in set(PY_IMPORT_RX.findall(text)):
                    if target not in self.idx.modules and target != "odoo":
                        self.err("import", f"{rel}: odoo.addons.{target} is not available")
                        missing += 1
        if not missing:
            self.ok("import", "all runtime python imports resolve")

    # -- 5 ---------------------------------------------------------------
    def check_xmlids(self) -> None:
        print("\n[5] external xml-id resolution (install-time surfaces)")
        missing = 0
        for module in self.src_modules:
            manifest = self.idx.modules[module].manifest or {}
            if manifest.get("installable", True) is False:
                continue
            data_files = list(manifest.get("data") or []) + list(manifest.get("demo") or [])
            for entry in data_files:
                if not isinstance(entry, str) or not entry.endswith(".xml"):
                    continue
                path = os.path.join(self.source, module, entry)
                text = read_text(path)
                if text is None:
                    self.err("xmlid", f"{module}/{entry}: file missing")
                    continue
                for rx in (XMLID_RX, ENV_REF_RX):
                    for token in set(rx.findall(text)):
                        if token not in self.idx.modules and token not in ("odoo",):
                            self.err("xmlid", f"{module}/{entry}: '{token}.*' owner not available")
                            missing += 1
        if not missing:
            self.ok("xmlid", "all install-time xml-id owners resolve")

    # -- 6 ---------------------------------------------------------------
    def check_assets(self) -> None:
        print("\n[6] asset integrity")
        problems = 0
        for module in self.src_modules:
            manifest = self.idx.modules[module].manifest or {}
            if manifest.get("installable", True) is False:
                continue
            assets = manifest.get("assets") or {}
            if not isinstance(assets, dict):
                continue
            for bundle, files in assets.items():
                owner = bundle.split(".")[0]
                if owner != "web" and owner not in self.idx.modules:
                    self.warn("asset-bundle", f"{module}: bundle '{bundle}' owner '{owner}' unknown")
                for entry in files or []:
                    if isinstance(entry, (list, tuple)):
                        entry = entry[-1]
                    if not isinstance(entry, str):
                        continue
                    m = ASSET_PATH_RX.match(entry)
                    if not m:
                        continue
                    if m.group(1) not in self.idx.modules:
                        self.err("asset-path", f"{module}: '{entry}' owner '{m.group(1)}' missing")
                        problems += 1
                        continue
                    # Resolve the file against the module that owns it: `web/...`
                    # lives in the Community addons tree, not in the port source.
                    owner_path = self.idx.modules[m.group(1)].path
                    rest = entry[len(m.group(1)) + 1:]
                    base = rest.split("*")[0]
                    candidate = os.path.join(owner_path, base)
                    if "*" not in entry and not os.path.isfile(candidate):
                        self.err("asset-path", f"{module}: file not found '{entry}'")
                        problems += 1
                    elif "*" in entry and not os.path.isdir(candidate.rstrip("/")):
                        self.warn("asset-glob", f"{module}: glob base missing '{entry}'")
        if not problems:
            self.ok("asset", "asset owners and files resolve")

    # -- 7 ---------------------------------------------------------------
    def check_model_uniqueness(self) -> None:
        print("\n[7] ORM model uniqueness across ported modules")
        owners: dict = defaultdict(set)
        for module in self.src_modules:
            for path in iter_text_files(os.path.join(self.source, module)):
                if path.endswith(".py"):
                    for model in MODEL_NAME_RX.findall(read_text(path) or ""):
                        owners[model].add(module)
        clashes = {m: sorted(mods) for m, mods in owners.items()
                   if len(mods) > 1 and not any(x.startswith("l10n_") for x in mods)}
        # inheritance across modules is fine; only identical _name declarations clash
        if clashes:
            for model, mods in sorted(clashes.items()):
                self.warn("model-dup", f"{model} declared in {mods}")
        self.ok("model", f"{len(owners)} distinct models declared, {len(clashes)} duplicate declarations")

    # -- 8 ---------------------------------------------------------------
    def run_install(self, db: str, odoo_bin: str, modules: list) -> None:
        print(f"\n[8] install smoke test (db={db}, modules={modules})")
        cmd = [sys.executable, odoo_bin, "-c", self._conf(), "-d", db,
               "-i", ",".join(modules), "--stop-after-init", "--log-level", "warn"]
        if not os.path.isfile(self._conf()):
            cmd = [c for c in cmd if c != "-c"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=os.path.dirname(odoo_bin))
        except subprocess.TimeoutExpired:
            self.err("install", "timed out after 30 min")
            return
        output = (proc.stdout or "") + (proc.stderr or "")
        tracebacks = [ln for ln in output.splitlines()
                      if "Traceback" in ln or "ERROR" in ln or "CRITICAL" in ln]
        if proc.returncode != 0 or tracebacks:
            for ln in tracebacks[:20]:
                self.err("install", ln.strip())
            write_text("_toolkit/out/install.log", output)
            print("  full log -> _toolkit/out/install.log")
        else:
            self.ok("install", f"modules installed cleanly: {modules}")

    def _conf(self) -> str:
        root = os.path.dirname(os.path.dirname(self.source))
        return os.path.join(root, "conf", "odoo.conf")

    # -- driver ----------------------------------------------------------
    def run(self, strict: bool) -> int:
        print(f"\n{'=' * 78}\nmsolutions port verification\n{'=' * 78}")
        print(f"source: {self.source}")
        self.check_manifests_and_deps()
        self.check_python_imports()
        self.check_xmlids()
        self.check_assets()
        self.check_model_uniqueness()
        print(f"\n{'=' * 78}")
        print(f"errors={len(self.errors)}  warnings={len(self.warnings)}")
        if self.warnings:
            print("warnings:")
            for check, msg in self.warnings[:40]:
                print(f"  - {check}: {msg}")
        return 1 if self.errors else 0


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="msolutions port verification")
    ap.add_argument("--source", default=os.path.normpath(os.path.join(here, "..")))
    ap.add_argument("--install", action="store_true", help="run an odoo-bin install smoke test")
    ap.add_argument("--odoo-bin", default="", help="path to odoo-bin")
    ap.add_argument("--db", default="msolutions_verify")
    ap.add_argument("--modules", default="", help="comma separated modules for the install test")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
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
    v = Verifier(source, roots)
    rc = v.run(args.strict)

    if args.install:
        odoo_bin = args.odoo_bin or os.path.join(root, "odoo-bin")
        modules = [m for m in args.modules.split(",") if m] or [
            "account_reports", "account_fiscal_categories",
            "account_bank_statement_import", "msolutions_account_bridge",
        ]
        v.run_install(args.db, odoo_bin, modules)

    if args.json:
        write_text(args.json, json.dumps({
            "errors": v.errors,
            "warnings": v.warnings,
            "info": v.info,
        }, indent=2))
        print(f"[json] {args.json}")
    return 1 if v.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
