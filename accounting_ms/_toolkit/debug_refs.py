#!/usr/bin/env python3
"""Debug helper: show why a module is considered to reference absorbed symbols."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase1_refactor import ABSORBED, UNEMULATED, Refactor  # noqa: E402

here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
root = os.path.dirname(os.path.dirname(here))
roots = {
    "core": root + "/odoo/addons",
    "addons": root + "/addons",
    "enterprise": root + "/enterprise/addons",
    "enterprise_alt": root + "/custom/addions_enterprise",
    "source": here,
}
rf = Refactor(here, roots, apply=False, backup=False, mode="brand",
              quarantine=False, brand={"author": "x", "website": "x", "category": "x",
                                       "license": "x", "icon_rel": "x"}, verbose=False)

for mod in sys.argv[1:]:
    text = rf._module_text(mod)
    print(f"\n### {mod}")
    for token in sorted(ABSORBED | UNEMULATED):
        esc = re.escape(token)
        hits = []
        for rx, label in [
            (rf"['\"]{esc}\.[A-Za-z_]", "quoted"),
            (rf"odoo\.addons\.{esc}[.\s]", "import"),
            (rf'=\s*["\']{esc}\.[A-Za-z_]', "xmlattr"),
        ]:
            m = re.search(rx, text)
            if m:
                hits.append((label, text[max(0, m.start() - 50):m.end() + 30]))
        for model in rf._models_of(token):
            m = re.search(rf"['\"]{re.escape(model)}['\"]", text)
            if m:
                hits.append((f"model:{model}", text[max(0, m.start() - 50):m.end() + 30]))
        if hits:
            print(f"  {token}:")
            for label, ctx in hits[:3]:
                print(f"    [{label}] ...{ctx!r}")
