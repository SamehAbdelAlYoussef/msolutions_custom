#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase1_refactor.py
==================
Phase 1 of the msolutions accounting port.

What it does, per module under ``--source``:

1. **Dependency sanitise**
   * Removes ``web_enterprise`` (optionally swapped for ``web``).
   * Rewrites Enterprise-only dependencies (``mail_enterprise`` -> ``mail``).
   * Drops dependencies that are not resolvable on a stock Community addons
     path and are *absorbed* by ``msolutions_account_bridge``.
   * Flags dependencies that cannot be emulated at all (``documents``,
     ``knowledge``, ``ai``, ``sign``, ``iap_extract``) and, when
     ``--quarantine`` is set, marks the dependent module ``installable: False``
     instead of leaving a guaranteed-broken module in the registry.
   * Injects the bridge dependency for every module that consumes an absorbed
     Enterprise symbol.

2. **Branding override**
   * author / website / category / license rewritten to the msolutions brand.
   * A generated branded icon is written to ``static/description/icon.png``.
   * ``icon`` manifest key normalised to the module-local icon.

3. **Rename engine** (opt-in, never destructive by default)
   * ``--mode brand``  : keep module directory names (default, safest).
   * ``--mode safe``   : rename only modules with zero inbound references and
                          no collision with a Community core module.
   * ``--mode full``   : rename the whole suite and rewrite ``old.id`` /
                          ``old/static/`` references across the tree.

4. **Umbrella wrapper** ``msolutions_accounting`` is (re)generated with a
   Community-safe dependency set.

Every mutated file is backed up under ``_toolkit/.backup/<timestamp>/`` unless
``--no-backup`` is given.

Usage
-----
    python3 _toolkit/phase1_refactor.py --dry-run
    python3 _toolkit/phase1_refactor.py --apply --mode brand
    python3 _toolkit/phase1_refactor.py --apply --mode safe --quarantine

Author : msolutions
License: OPL-1
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import (  # noqa: E402
    BRAND,
    ENTERPRISE_BUNDLE_MAP,
    MODULE_PREFIX,
    ModuleIndex,
    dump_manifest,
    generate_icon,
    iter_text_files,
    load_manifest,
    manifest_header,
    read_text,
    write_text,
)

BRIDGE_MODULE = "msolutions_account_bridge"
UMBRELLA_MODULE = "msolutions_accounting"
CE_KINDS = {"core", "addons", "source"}

# Modules the bridge itself depends on. They must never depend on the bridge,
# otherwise the registry build hits a cyclic dependency.
BRIDGE_DEPENDS: Set[str] = {"base", "web", "account", "mail"}

# Enterprise-only modules whose API surface the bridge emulates with real stubs.
ABSORBED: Set[str] = {
    "account_reports",
    "account_bank_statement_import",
    "account_reconciliation_widget",
    "account_fiscal_categories",
    "stock_accountant",
    "sale_external_tax",
    "project_enterprise",
    "web_enterprise",
    "mail_enterprise",
}

# Enterprise-only modules that cannot be faithfully emulated. Modules depending
# on them are quarantined (installable: False) when --quarantine is active.
UNEMULATED: Set[str] = {
    "documents",
    "knowledge",
    "ai",
    "sign",
    "iap_extract",
    # `appointment` is a whole Enterprise app whose controllers are imported
    # directly (`from odoo.addons.appointment.controllers... import ...`), so it
    # cannot be stubbed - dependents must be quarantined.
    "appointment",
}

# Hard overrides applied to `depends` regardless of where the module resolves.
DEP_OVERRIDES: Dict[str, Optional[str]] = {
    "web_enterprise": None,
    "mail_enterprise": "mail",
    "account_reconciliation_widget": None,
    "account_reports": None,
    "account_bank_statement_import": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class Change:
    module: str
    kind: str
    detail: str


class Refactor:
    def __init__(self, source: str, roots: Dict[str, str], apply: bool,
                 backup: bool, mode: str, quarantine: bool, brand: Dict[str, str],
                 verbose: bool = True, isolate_apps: bool = True):
        self.source = os.path.abspath(source)
        self.roots = roots
        self.apply = apply
        self.backup = backup
        self.mode = mode
        self.quarantine = quarantine
        self.brand = brand
        self.verbose = verbose
        self.isolate_apps = isolate_apps
        self.idx = ModuleIndex(roots)
        self.src_modules = sorted(n for n, i in self.idx.modules.items()
                                  if i.root_kind == "source")
        self.changes: List[Change] = []
        self.rename_map: Dict[str, str] = {}
        self.quarantined: Set[str] = set()
        self.absorbed_users: Dict[str, Set[str]] = defaultdict(set)
        self._text_cache: Dict[str, str] = {}
        self._model_cache: Dict[str, Set[str]] = {}
        self._ce_models: Optional[Set[str]] = None
        self.cycle_risks: Set[str] = set()
        self.baseline: Dict[str, list] = {}
        self.bridge_backup_dir = os.path.join(
            self.source, "_toolkit", ".backup", time.strftime("%Y%m%d-%H%M%S"))

    # -- reporting -----------------------------------------------------------
    def log(self, module: str, kind: str, detail: str) -> None:
        self.changes.append(Change(module, kind, detail))
        if self.verbose:
            print(f"  [{kind:<12}] {module:<34} {detail}")

    # -- io ------------------------------------------------------------------
    def _backup(self, path: str) -> None:
        if not self.backup or not self.apply:
            return
        rel = os.path.relpath(path, self.source)
        dest = os.path.join(self.bridge_backup_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isfile(path):
            shutil.copy2(path, dest)

    def emit(self, path: str, content: str) -> None:
        if not self.apply:
            return
        self._backup(path)
        write_text(path, content)

    # -- dependency analysis -------------------------------------------------
    def _is_enterprise_only(self, dep: str) -> bool:
        """True only when *dep* cannot resolve on a stock Community path.

        In-tree (``source``) modules are always resolvable and must never be
        treated as Enterprise-only.
        """
        info = self.idx.modules.get(dep)
        if info is None:
            return True
        return info.root_kind not in CE_KINDS

    def _module_text(self, module: str) -> str:
        """Concatenated text of every file in *module* (cached)."""
        cached = self._text_cache.get(module)
        if cached is not None:
            return cached
        root = os.path.join(self.source, module)
        chunks: List[str] = []
        if module in self.src_modules and os.path.isdir(root):
            for path in iter_text_files(root):
                if os.path.basename(path) == "__manifest__.py":
                    continue
                text = read_text(path)
                if text:
                    chunks.append(text)
        joined = "\n".join(chunks)
        self._text_cache[module] = joined
        return joined

    def _module_references(self, module: str, tokens: Iterable[str]) -> bool:
        """True when *module* references ``token.`` as a real symbol.

        Word-boundary anchored so short module names do not produce false
        positives (e.g. ``design.`` must not match ``sign.``).
        """
        text = self._module_text(module)
        if not text:
            return False
        return any(self._has_symbol(text, t) for t in tokens)

    # -- symbol detection ----------------------------------------------------
    # NOTE: the leading group must be a real attribute boundary, otherwise
    # `comodel_name = 'x'` would be parsed as a model declaration.
    _NAME_RX = re.compile(r"(?<![A-Za-z0-9_])_name\s*=\s*['\"]([\w.]+)['\"]")
    _INHERIT_SINGLE_RX = re.compile(r"(?<![A-Za-z0-9_])_inherit\s*=\s*['\"]([\w.]+)['\"]")
    _INHERIT_LIST_RX = re.compile(r"(?<![A-Za-z0-9_])_inherit\s*=\s*\[([^\]]*)\]")

    def _build_ce_models(self) -> None:
        """Collect every model owned by a Community module.

        Enterprise modules sometimes re-declare a Community model (``ai``
        declares ``mail.thread``, ``sale_external_tax`` declares
        ``sale.order``). Subtracting this set prevents referencing
        ``sale.order`` from looking like an Enterprise dependency.
        """
        if self._ce_models is not None:
            return
        models: Set[str] = set()
        for kind in ("core", "addons", "source"):
            root = self.roots.get(kind)
            if not root or not os.path.isdir(root):
                continue
            for entry in sorted(os.listdir(root)):
                mpath = os.path.join(root, entry)
                if not os.path.isfile(os.path.join(mpath, "__manifest__.py")):
                    continue
                for path in iter_text_files(mpath):
                    if path.endswith(".py"):
                        models |= set(self._NAME_RX.findall(read_text(path) or ""))
        self._ce_models = models

    def _models_of(self, modname: str) -> Set[str]:
        """ORM models **effectively owned** by *modname*.

        ``_inherit`` is deliberately excluded: extending ``account.move`` does
        not mean the module owns it. Models that a Community module also
        declares are attributed to Community, never to Enterprise.
        """
        if modname in self._model_cache:
            return self._model_cache[modname]
        models: Set[str] = set()
        info = self.idx.modules.get(modname)
        if info is not None:
            for path in iter_text_files(info.path):
                if path.endswith(".py"):
                    models |= set(self._NAME_RX.findall(read_text(path) or ""))
            if info.root_kind not in CE_KINDS:
                self._build_ce_models()
                models -= (self._ce_models or set())
        self._model_cache[modname] = models
        return models

    def _has_symbol(self, text: str, token: str) -> bool:
        """Context-anchored test for a real ``token`` reference (not prose)."""
        esc = re.escape(token)
        # quoted xml-id / dotted path: 'account_reports.balance_sheet' or "documents.document"
        if re.search(rf"['\"]{esc}\.[A-Za-z_]", text):
            return True
        # python import: from odoo.addons.account_reports.models... import ...
        if re.search(rf"odoo\.addons\.{esc}[.\s]", text):
            return True
        # xml attribute: ref="account_reports.x" / inherit_id="..." / groups="..."
        if re.search(rf'=\s*["\']{esc}\.[A-Za-z_]', text):
            return True
        # model-name reference (e.g. _inherit = 'account.report')
        for model in self._models_of(token):
            if re.search(rf"['\"]{re.escape(model)}['\"]", text):
                return True
        return False

    def sanitise_depends(self, module: str, manifest: dict) -> Tuple[list, bool]:
        deps = list(manifest.get("depends") or [])
        original = list(deps)
        out: List[str] = []
        absorbed_used: Set[str] = set()
        unemulated_used: Set[str] = set()

        for dep in deps:
            # An in-source module always wins over a stripping rule: this is what
            # lets the msolutions compatibility shims (account_reports,
            # account_bank_statement_import, ...) transparently replace the
            # Enterprise originals while keeping their technical names.
            dep_info = self.idx.modules.get(dep)
            if dep_info is not None and dep_info.root_kind == "source":
                out.append(dep)
                continue
            override = DEP_OVERRIDES.get(dep, "KEEP")
            if override is None:
                # hard-dropped: absorbed by the bridge unless unemulated
                if dep in UNEMULATED:
                    unemulated_used.add(dep)
                    self.log(module, "dep-drop", f"{dep} (unemulated)")
                else:
                    if self._module_references(module, [dep]):
                        absorbed_used.add(dep)
                    self.log(module, "dep-drop", dep)
                continue
            if override == "KEEP":
                if self._is_enterprise_only(dep):
                    # Enterprise-only dependency discovered dynamically.
                    if dep in UNEMULATED:
                        unemulated_used.add(dep)
                        self.log(module, "dep-drop", f"{dep} (unemulated)")
                    else:
                        absorbed_used.add(dep)
                        self.log(module, "dep-drop", f"{dep} (absorbed)")
                    continue
                out.append(dep)
            else:
                self.log(module, "dep-map", f"{dep} -> {override}")
                if self._module_references(module, [dep]):
                    absorbed_used.add(dep)
                out.append(override)

        # de-duplicate preserving order
        seen: Set[str] = set()
        deduped: List[str] = []
        for d in out:
            if d not in seen:
                seen.add(d)
                deduped.append(d)

        # modules that consume absorbed enterprise symbols need the bridge
        # Only symbols that are NOT provided by an in-source shim require the
        # bridge; account_reports/account_fiscal_categories/... now resolve
        # locally and must not drag the bridge in.
        unresolved = {
            token for token in (ABSORBED | UNEMULATED)
            if not (self.idx.modules.get(token)
                    and self.idx.modules[token].root_kind == "source")
        }
        needs_bridge = bool(absorbed_used) or (
            bool(unresolved) and self._module_references(module, unresolved)
        )
        if (needs_bridge and module not in ABSORBED
                and module != BRIDGE_MODULE and BRIDGE_MODULE not in deduped):
            if module in BRIDGE_DEPENDS:
                # The bridge depends on this module, so injecting it back would
                # create an import cycle. The detected reference is soft
                # (a string comparison / logger name) or must be neutralised by
                # Phase 3 instead.
                self.cycle_risks.add(module)
                self.log(module, "bridge-skip",
                         "consumes absorbed symbols but is a bridge dependency")
            else:
                deduped.insert(0, BRIDGE_MODULE)
                self.log(module, "dep-add",
                         f"{BRIDGE_MODULE} (absorbed: {sorted(absorbed_used) or 'symbol-wise'})")

        self.absorbed_users[module] |= absorbed_used
        changed = deduped != original or unemulated_used
        if unemulated_used and self.quarantine:
            self.quarantined.add(module)
        return deduped, changed

    # -- assets --------------------------------------------------------------
    def remap_assets(self, module: str, manifest: dict) -> bool:
        assets = manifest.get("assets")
        if not isinstance(assets, dict):
            return False
        changed = False
        new_assets: Dict[str, list] = {}
        for bundle, files in assets.items():
            target = bundle
            owner = bundle.split(".")[0]
            if bundle in ENTERPRISE_BUNDLE_MAP:
                target = ENTERPRISE_BUNDLE_MAP[bundle]
            elif owner not in self.idx.modules and owner not in ("web",):
                target = "web.assets_backend"
            if target != bundle:
                self.log(module, "asset-bundle", f"{bundle} -> {target}")
                changed = True
            bucket = new_assets.setdefault(target, [])
            if isinstance(files, list):
                bucket.extend(files)
        if changed:
            manifest["assets"] = new_assets
        return changed

    # -- branding ------------------------------------------------------------
    def brand_manifest(self, module: str, manifest: dict) -> bool:
        changed = False
        applied: List[str] = []
        for key, value in (("author", self.brand["author"]),
                           ("website", self.brand["website"]),
                           ("category", self.brand["category"]),
                           ("license", self.brand["license"])):
            if manifest.get(key) != value:
                manifest[key] = value
                applied.append(key)
                changed = True
        icon_url = f"/{module}/static/description/icon.png"
        if manifest.get("icon") != icon_url:
            manifest["icon"] = icon_url
            applied.append("icon")
            changed = True
        if changed:
            self.log(module, "brand", ",".join(applied))

        # icon file
        icon_path = os.path.join(self.source, module, self.brand["icon_rel"])
        if not os.path.isfile(icon_path):
            if self.apply:
                generate_icon(icon_path, 128)
            self.log(module, "icon", "generated static/description/icon.png")
            changed = True
        return changed

    # -- application-card isolation ------------------------------------------
    def isolate_app(self, module: str, manifest: dict) -> bool:
        """Ensure only the umbrella module shows as an application card.

        Odoo's Apps dashboard filters on ``application = True``. Leaving the
        Enterprise modules flagged as applications puts a native "Accounting"
        card next to ``msolutions Accounting``, so every ported module except
        the umbrella is demoted to a plain module. Dependencies and backend
        logic are untouched - only the dashboard visibility changes.
        """
        if not self.isolate_apps:
            return False
        desired = module == UMBRELLA_MODULE
        if bool(manifest.get("application", False)) != desired:
            manifest["application"] = desired
            self.log(module, "app-card",
                     f"application={desired}"
                     + (" (sole suite app card)" if desired else " (hidden from Apps dashboard)"))
            return True
        return False

    # -- rename engine -------------------------------------------------------
    def compute_renames(self) -> Dict[str, str]:
        if self.mode == "brand":
            return {}
        # Single pass: read each file once and probe every module name against it.
        inbound = defaultdict(int)
        probes = {n: (f"{n}.", f"{n}/static") for n in self.src_modules}
        for path in iter_text_files(self.source):
            text = read_text(path)
            if not text:
                continue
            owner = next((n for n in self.src_modules
                          if f"{os.sep}{n}{os.sep}" in path), None)
            for name, (dot, static) in probes.items():
                if name == owner:
                    continue
                if dot in text or static in text:
                    inbound[name] += 1

        mapping: Dict[str, str] = {}
        for name in self.src_modules:
            if name in (BRIDGE_MODULE, UMBRELLA_MODULE) or name in UNEMULATED:
                continue
            info = self.idx.modules.get(name)
            # never rename a module Community already ships under the same name
            if info and info.root_kind != "source":
                continue
            if self.mode == "safe" and inbound[name]:
                continue
            mapping[name] = f"{MODULE_PREFIX}{name}"
        return mapping

    def apply_renames(self) -> None:
        self.rename_map = self.compute_renames()
        if not self.rename_map:
            return
        for old, new in self.rename_map.items():
            old_dir = os.path.join(self.source, old)
            new_dir = os.path.join(self.source, new)
            self.log(old, "rename", f"-> {new}")
            if self.apply and os.path.isdir(old_dir) and not os.path.exists(new_dir):
                os.rename(old_dir, new_dir)
        # rewrite references old.id -> new.id and old/static -> new/static
        if not self.apply:
            return
        for path in iter_text_files(self.source):
            text = read_text(path)
            if text is None:
                continue
            original = text
            for old, new in self.rename_map.items():
                text = re.sub(rf"(?<![\w.]){re.escape(old)}(\.[A-Za-z_])", rf"{new}\1", text)
                text = text.replace(f"{old}/static/", f"{new}/static/")
                text = re.sub(rf"(['\"]){re.escape(old)}(['\"])", rf"\1{new}\2", text)
            if text != original:
                self.emit(path, text)

    def rewrite_manifest_depends(self) -> None:
        """Apply rename_map to depends entries of every source manifest."""
        if not self.rename_map:
            return
        for module in self.src_modules:
            new_module = self.rename_map.get(module, module)
            mpath = os.path.join(self.source, new_module, "__manifest__.py")
            if not os.path.isfile(mpath):
                continue
            src = read_text(mpath) or ""
            manifest = load_manifest(mpath)
            deps = [self.rename_map.get(d, d) for d in (manifest.get("depends") or [])]
            if deps != (manifest.get("depends") or []):
                manifest["depends"] = deps
                self.emit(mpath, dump_manifest(manifest, manifest_header(src)))

    # -- pristine dependency baseline ---------------------------------------
    def _baseline_path(self) -> str:
        return os.path.join(self.source, "_toolkit", "out", "depends_baseline.json")

    def load_baseline(self) -> None:
        """Load (or seed) the pristine ``depends`` baseline.

        Sanitising ``depends`` is destructive: once an Enterprise dependency is
        removed it can no longer be detected. To stay idempotent, the original
        dependency lists are recorded once - seeded from the earliest backup -
        and every run sanitises from that baseline instead of from the already
        rewritten manifests.
        """
        path = self._baseline_path()
        if os.path.isfile(path):
            try:
                self.baseline = json.loads(read_text(path) or "{}")
                return
            except ValueError:
                self.baseline = {}
        # Seed from the earliest Phase 1 backup, if any.
        bkroot = os.path.join(self.source, "_toolkit", ".backup")
        if os.path.isdir(bkroot):
            for bk in sorted(os.listdir(bkroot)):
                for manifest_path in glob.glob(
                        os.path.join(bkroot, bk, "**", "__manifest__.py"), recursive=True):
                    rel = os.path.relpath(manifest_path, os.path.join(bkroot, bk))
                    module = rel.split(os.sep)[0]
                    try:
                        self.baseline[module] = load_manifest(manifest_path).get("depends") or []
                    except Exception:  # noqa: BLE001
                        continue

    def save_baseline(self) -> None:
        if not self.apply:
            return
        write_text(self._baseline_path(), json.dumps(self.baseline, indent=2, sort_keys=True))

    # -- external python dependencies ---------------------------------------
    @staticmethod
    def missing_python_deps(manifest: dict) -> List[str]:
        """Return the manifest ``external_dependencies`` python packages that
        cannot be imported in the current interpreter.

        A module with an unmet python dependency aborts the *entire* install
        with ``UserError: ... external dependency is not met`` when it is pulled
        in by the umbrella, so such modules must be quarantined.
        """
        import importlib.util

        missing: List[str] = []
        ext = manifest.get("external_dependencies") or {}
        if not isinstance(ext, dict):
            return missing
        for package in ext.get("python", []) or []:
            if importlib.util.find_spec(package) is None:
                missing.append(package)
        return missing

    # -- umbrella wrapper ----------------------------------------------------
    def build_umbrella(self) -> None:
        path = os.path.join(self.source, UMBRELLA_MODULE)
        installable = [
            self.rename_map.get(m, m) for m in self.src_modules
            if m not in self.quarantined
            and m not in UNEMULATED
            and m != UMBRELLA_MODULE
            and self.idx.modules[m].manifest
            and self.idx.modules[m].manifest.get("installable", True)
        ]
        # never self-depend; drop modules that are integrations of unemulated deps
        installable = sorted(set(installable))
        manifest = {
            "name": "msolutions Accounting",
            "version": "19.0.1.0.0",
            "category": self.brand["category"],
            "summary": "msolutions-branded Accounting suite for Odoo Community",
            "author": self.brand["author"],
            "website": self.brand["website"],
            "license": self.brand["license"],
            "depends": installable,
            # Consolidates the Enterprise + shim root menus under one
            # "msolutions Accounting" app entry (see the file for details).
            "data": ["views/msolutions_accounting_menus.xml"],
            # Sole application card of the suite: installing this module walks
            # the dependency graph and installs/updates every ported module.
            "application": True,
            "auto_install": False,
            "installable": True,
            # Sort the card first among the installed applications.
            "sequence": 1,
            "icon": f"/{UMBRELLA_MODULE}/static/description/icon.png",
        }
        header = "# -*- coding: utf-8 -*-\n# msolutions - branded Community accounting distribution.\n"
        self.emit(os.path.join(path, "__manifest__.py"), dump_manifest(manifest, header))
        self.emit(os.path.join(path, "__init__.py"), "")
        if self.apply:
            generate_icon(os.path.join(path, "static/description/icon.png"), 128)
        self.log(UMBRELLA_MODULE, "wrapper", f"depends on {len(installable)} modules")

    # -- quarantine propagation ---------------------------------------------
    def propagate_quarantine(self) -> None:
        """Installability is transitive: a module that depends on a quarantined
        module can never be installed, so quarantine it as well."""
        changed = True
        while changed:
            changed = False
            for name in self.src_modules:
                if name in self.quarantined or name == UMBRELLA_MODULE:
                    continue
                deps = (self.idx.modules[name].manifest or {}).get("depends") or []
                if any(d in self.quarantined for d in deps):
                    self.quarantined.add(name)
                    self.log(name, "quarantine", "installable=False (transitive)")
                    changed = True

    # -- driver --------------------------------------------------------------
    def run(self) -> int:
        print(f"\n=== Phase 1 refactor | mode={self.mode} apply={self.apply} "
              f"quarantine={self.quarantine} ===")
        print(f"source: {self.source}")
        if self.apply and self.backup:
            print(f"backup: {self.bridge_backup_dir}")

        # Pass 1 - analyse & sanitise every manifest in memory.
        self.load_baseline()
        prepared: Dict[str, dict] = {}
        headers: Dict[str, str] = {}
        for module in self.src_modules:
            info = self.idx.modules[module]
            mpath = os.path.join(self.source, module, "__manifest__.py")
            if info.manifest is None:
                print(f"  [SKIP] {module}: unparsable manifest ({info.manifest_error})")
                continue
            manifest = dict(info.manifest)
            # Always sanitise from the pristine dependency list, never from an
            # already-rewritten manifest, so the run is idempotent.
            if self.baseline.get(module):
                manifest["depends"] = list(self.baseline[module])
            else:
                self.baseline[module] = list(manifest.get("depends") or [])
            headers[module] = manifest_header(read_text(mpath) or "")
            deps, _ = self.sanitise_depends(module, manifest)
            manifest["depends"] = deps
            self.remap_assets(module, manifest)
            self.brand_manifest(module, manifest)
            # Only msolutions_accounting stays an application card.
            self.isolate_app(module, manifest)
            # Unmet python external dependencies abort the whole install when the
            # umbrella pulls the module in, so quarantine them up front.
            if self.quarantine:
                unmet = self.missing_python_deps(manifest)
                if unmet:
                    self.quarantined.add(module)
                    self.log(module, "quarantine-ext",
                             f"unmet python deps {unmet}")
            prepared[module] = manifest

        # Persist the pristine baseline so subsequent runs stay idempotent.
        self.save_baseline()

        # Pass 2 - propagate quarantine to fixpoint before anything is written.
        self.propagate_quarantine()

        # Pass 3 - write manifests.
        for module, manifest in prepared.items():
            mpath = os.path.join(self.source, module, "__manifest__.py")
            if module in self.quarantined and manifest.get("installable", True):
                manifest["installable"] = False
                summary = (manifest.get("summary") or "").strip()
                manifest["summary"] = (
                    summary + " [msolutions: requires an unemulated Enterprise module]"
                ).strip()
                self.log(module, "quarantine", "installable=False")
            new_src = dump_manifest(manifest, headers.get(module, ""))
            if new_src != (read_text(mpath) or ""):
                self.emit(mpath, new_src)

        self.apply_renames()
        self.rewrite_manifest_depends()
        self.build_umbrella()

        print(f"\n=== summary ===")
        by_kind = defaultdict(int)
        for ch in self.changes:
            by_kind[ch.kind] += 1
        for kind, cnt in sorted(by_kind.items()):
            print(f"  {kind:<14} {cnt}")
        print(f"  quarantined   : {sorted(self.quarantined)}")
        print(f"  renamed       : {len(self.rename_map)}")
        if not self.apply:
            print("\n[dry-run] nothing written. Re-run with --apply.")
        return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="msolutions Phase 1 refactor")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--source", default=os.path.normpath(os.path.join(here, "..")))
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry-run (default)")
    ap.add_argument("--mode", choices=("brand", "safe", "full"), default="brand")
    ap.add_argument("--quarantine", action="store_true",
                    help="mark modules needing unemulated Enterprise deps installable=False")
    ap.add_argument("--isolate-apps", action=argparse.BooleanOptionalAction, default=True,
                    help="keep only msolutions_accounting as an application card "
                         "(default: enabled)")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--author", default=BRAND["author"])
    ap.add_argument("--website", default=BRAND["website"])
    ap.add_argument("--category", default=BRAND["category"])
    ap.add_argument("--license", dest="license", default=BRAND["license"])
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    roots = {
        "core": os.path.join(os.path.dirname(os.path.dirname(args.source)), "odoo", "addons"),
        "addons": os.path.join(os.path.dirname(os.path.dirname(args.source)), "addons"),
        "enterprise": os.path.join(os.path.dirname(os.path.dirname(args.source)), "enterprise", "addons"),
        "enterprise_alt": os.path.join(os.path.dirname(os.path.dirname(args.source)), "custom", "addions_enterprise"),
        "source": os.path.abspath(args.source),
    }
    brand = {
        "author": args.author,
        "website": args.website,
        "category": args.category,
        "license": args.license,
        "icon_rel": BRAND["icon_rel"],
    }
    rf = Refactor(
        source=args.source, roots=roots, apply=args.apply and not args.dry_run,
        backup=not args.no_backup, mode=args.mode, quarantine=args.quarantine,
        brand=brand, verbose=not args.quiet, isolate_apps=args.isolate_apps,
    )
    rc = rf.run()
    if args.json:
        payload = {
            "mode": args.mode,
            "applied": bool(args.apply and not args.dry_run),
            "renames": rf.rename_map,
            "quarantined": sorted(rf.quarantined),
            "absorbed_users": {k: sorted(v) for k, v in rf.absorbed_users.items()},
            "changes": [c.__dict__ for c in rf.changes],
        }
        write_text(args.json, json.dumps(payload, indent=2))
        print(f"[json] {args.json}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
