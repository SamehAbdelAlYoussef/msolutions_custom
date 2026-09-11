#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
msolutions - Phase 5: port Odoo Enterprise report definitions.

Odoo 19 Community already ships the whole `account.report` data model
(`account.report`, `.line`, `.expression`, `.column`) including the formula
shortcuts (`domain_formula`, `aggregation_formula`, ...). What Community does
NOT ship is (a) the XML definitions of the individual reports and (b) the
evaluation engine.

This script copies the report definition files from the Enterprise source into
the `account_reports` compatibility shim, stripping the fields that only exist
in Enterprise. The evaluation engine lives in the shim's Python code.

Usage:
    python3 _toolkit/port_report_defs.py --list
    python3 _toolkit/port_report_defs.py --apply balance_sheet profit_and_loss
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.normpath(os.path.join(HERE, ".."))
ENTERPRISE = os.path.normpath(
    os.path.join(SOURCE, "..", "..", "custom", "addions_enterprise", "account_reports")
)
SHIM = os.path.join(SOURCE, "account_reports")

# Fields that exist only on the Enterprise `account.report` / `account.report.line`
# / `account.report.expression` models. Community raises on unknown fields, so
# they are removed while porting.
STRIP_FIELDS = {
    "filter_analytic_groupby",
    "allow_account_audit_status_on_lines",
    "custom_handler_model_id",
    "auditable",
    "filter_hide_0_lines",
    "search_bar",
    "load_more_limit",
    "filter_audit",
}

# Engine values Community knows about. Anything else in the ported files is
# reported so it can be implemented deliberately rather than silently ignored.
CE_ENGINES = {"domain", "tax_tags", "aggregation", "account_codes", "external", "custom"}

# Reports that are pure data (domain/aggregation/account_codes) and therefore
# work with the shim engine out of the box.
PORTABLE = [
    "balance_sheet",
    "profit_and_loss",
    "cash_flow_report",
    "executive_summary",
    "aged_partner_balance",
    "partner_ledger",
    "deferred_reports",
    "journal_report",
    "multicurrency_revaluation_report",
    "customer_statement",
    "followup_report",
]

# Reports driven by a `custom_handler_model_id` Python handler; they need code
# before their definitions are worth porting.
NEEDS_HANDLER = ["trial_balance", "general_ledger", "sales_report", "generic_tax_report"]


def _strip_field(text: str, field: str) -> tuple[str, int]:
    """Remove a <field name="field" .../> or <field name="field">...</field>."""
    n = 0
    # self-closing
    text, k = re.subn(r'[ \t]*<field\s+name="%s"[^>]*/>\n?' % re.escape(field), "", text)
    n += k
    # block form
    text, k = re.subn(
        r'[ \t]*<field\s+name="%s"\s*>.*?</field>\n?' % re.escape(field), "", text, flags=re.S
    )
    n += k
    return text, n


def sanitize(text: str, keep_handler: bool = False) -> tuple[str, dict]:
    report = {}
    fields = set(STRIP_FIELDS)
    if keep_handler:
        # The shim declares `custom_handler_model_id` itself and implements the
        # matching handler, so keep the reference for those reports.
        fields.discard("custom_handler_model_id")
    for field in sorted(fields):
        text, n = _strip_field(text, field)
        if n:
            report[field] = n

    engines = sorted(set(re.findall(r'<field name="engine">([a-z_]+)</field>', text)))
    unknown = [e for e in engines if e not in CE_ENGINES]
    if unknown:
        report["UNKNOWN_ENGINES"] = unknown

    handlers = sorted(set(re.findall(r'ref="(model_[a-z_0-9]+)"', text)))
    if handlers:
        report["handler_models_referenced"] = handlers

    header = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<!--\n"
        "    msolutions - ported from Odoo Enterprise `account_reports/data/%s`.\n"
        "    Enterprise-only fields were stripped so the file loads on Community.\n"
        "    The evaluation engine lives in account_reports/models/account_report.py.\n"
        "-->\n"
    )
    # drop the original xml declaration / comments already present
    body = re.sub(r"<\?xml[^>]*\?>\s*", "", text, count=1)
    return header + body, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list", action="store_true", help="show portable vs handler-driven reports")
    ap.add_argument("--keep-handler", action="store_true",
                    help="keep custom_handler_model_id (only for implemented handlers)")
    ap.add_argument("reports", nargs="*", default=[])
    args = ap.parse_args()

    if args.list or not args.reports:
        print("portable (domain/aggregation only):")
        for r in PORTABLE:
            p = os.path.join(ENTERPRISE, "data", f"{r}.xml")
            print(f"   {'OK ' if os.path.isfile(p) else 'MISSING'} {r}")
        print("\nneeds a custom handler first:")
        for r in NEEDS_HANDLER:
            p = os.path.join(ENTERPRISE, "data", f"{r}.xml")
            print(f"   {'OK ' if os.path.isfile(p) else 'MISSING'} {r}")
        if not args.reports:
            return 0

    os.makedirs(os.path.join(SHIM, "data"), exist_ok=True)
    rc = 0
    for report in args.reports:
        src = os.path.join(ENTERPRISE, "data", f"{report}.xml")
        if not os.path.isfile(src):
            print(f"  [skip ] {report}: no such file in Enterprise source")
            rc = 1
            continue
        with open(src, encoding="utf-8") as fh:
            raw = fh.read()
        clean, info = sanitize(raw, keep_handler=args.keep_handler)
        dst = os.path.join(SHIM, "data", f"{report}.xml")
        stripped = {k: v for k, v in info.items() if k != "UNKNOWN_ENGINES"}
        print(f"  [port ] {report}: stripped {stripped or '{}'}")
        if info.get("UNKNOWN_ENGINES"):
            print(f"           WARNING unknown engines: {info['UNKNOWN_ENGINES']}")
        if info.get("handler_models_referenced"):
            print(f"           handler models referenced: {info['handler_models_referenced']}")
        if args.apply:
            if os.path.isfile(dst):
                shutil.copy2(dst, dst + ".bak")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(clean)
    if not args.apply:
        print("\n(dry-run - pass --apply to write)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
