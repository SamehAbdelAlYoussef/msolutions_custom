#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
classify_xrefs.py
=================
For every xml-id the ported modules consume from Enterprise modules, determine
*how* it is used, so the bridge can emit a stub record of the correct type.

Usage classification
--------------------
inherit_id / t-inherit      -> ir.ui.view (extension target)
action (menuitem/@action)   -> ir.actions.act_window or ir.actions.client
t-call                      -> ir.ui.view (qweb template)
report_id / report ref      -> account.report
model=/ref= on <field>      -> depends on the owning field
groups=                     -> res.groups
asset bundle                -> assets declaration
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msolutions_common import iter_text_files, read_text  # noqa: E402
from phase1_refactor import ABSORBED, UNEMULATED  # noqa: E402

WATCH = sorted(ABSORBED | UNEMULATED)


def main() -> int:
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--source", default=os.path.normpath(os.path.join(here, "..")))
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    source = os.path.abspath(args.source)
    if args.json is None:
        pass

    kinds: dict = defaultdict(lambda: defaultdict(set))
    examples: dict = defaultdict(lambda: defaultdict(list))

    for path in iter_text_files(source):
        rel = os.path.relpath(path, source)
        module = rel.split(os.sep)[0]
        if module.startswith("_"):
            continue
        text = read_text(path) or ""
        for tok in WATCH:
            esc = re.escape(tok)
            # usage-aware patterns -> (kind, regex)
            patterns = [
                ("view_inherit", rf'(?:inherit_id|t-inherit)="({esc}\.[\w]+)"'),
                ("view_inherit", rf'\.(?:inherit_id|t-inherit)\s*=\s*["\']({esc}\.[\w]+)["\']'),
                ("action", rf'action="({esc}\.[\w]+)"'),
                ("template_call", rf't-call="({esc}\.[\w]+)"'),
                ("qweb_template", rf't-name="({esc}\.[\w]+)"'),
                ("account_report", rf'report_id[^>]*ref="({esc}\.[\w]+)"'),
                ("account_report", rf'"({esc}\.[\w]+)"\s*,\s*#\s*report'),
                ("group", rf'groups="({esc}\.[\w]+)"'),
                ("field_ref", rf'<field[^>]+ref="({esc}\.[\w]+)"'),
                ("env_ref", rf'env\.ref\(\s*["\']({esc}\.[\w]+)["\']'),
                ("xml_ref", rf'"({esc}\.[\w]+)"'),
                ("xml_ref", rf"'({esc}\.[\w]+)'"),
                ("xml_ref", rf'="({esc}\.[\w]+)"'),
            ]
            for kind, rx in patterns:
                for m in re.finditer(rx, text):
                    xid = m.group(1)
                    kinds[xid][kind].add(module)
                    if len(examples[xid][kind]) < 2:
                        line = text.count("\n", 0, m.start()) + 1
                        ctx = text[max(0, m.start() - 70):m.end() + 40].replace("\n", " ")
                        examples[xid][kind].append(f"{rel}:{line}  {ctx}")

    print("=" * 78)
    print("XREF USAGE CLASSIFICATION")
    print("=" * 78)
    for xid in sorted(kinds):
        kk = list(kinds[xid])
        mods = sorted({m for s in kinds[xid].values() for m in s})
        print(f"\n{xid}")
        print(f"  kinds : {', '.join(sorted(kk))}")
        print(f"  used  : {', '.join(mods)}")
        for kind in sorted(kk):
            for ex in examples[xid][kind][:1]:
                print(f"    [{kind}] {ex}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({k: {kk: sorted(v) for kk, v in d.items()} for k, d in kinds.items()},
                      fh, indent=2, sort_keys=True)
        print(f"\n[json] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
