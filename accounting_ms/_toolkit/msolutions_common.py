# -*- coding: utf-8 -*-
"""
msolutions_common
==================
Shared primitives for the msolutions accounting port toolkit.

This module is deliberately dependency-free (stdlib + optional Pillow) so it can
run under the Odoo virtualenv, the system interpreter, or CI.

Responsibilities
----------------
* Workspace / addons-path discovery.
* :class:`ModuleIndex` - which module lives in which addons root.
* Manifest read / write that survives Odoo's non-literal manifests.
* Dependency classification (CE-resolvable / enterprise-only / missing).
* Cross-reference scanning (XML ids, models, asset bundles, python refs).
* Brand icon generation.

Author : msolutions
License: OPL-1
"""

from __future__ import annotations

import ast
import json
import os
import pprint
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Brand configuration
# ---------------------------------------------------------------------------

MODULE_PREFIX = "msolutions_"

BRAND: Dict[str, str] = {
    "author": "msolutions",
    "website": "https://msolutions.example.com",
    "category": "msolutions Accounting",
    "license": "OPL-1",
    "icon_rel": "static/description/icon.png",
}

# Modules that live only in the Enterprise distribution. When the port targets a
# pure Community addons path these become unresolvable and must be either
# replaced by a CE equivalent or absorbed by the bridge module.
REPLACE_WITH_CE: Dict[str, Optional[str]] = {
    "web_enterprise": "web",
    "mail_enterprise": "mail",
    "account_reports": None,          # absorbed by msolutions_account_bridge
    "account_bank_statement_import": None,
    "account_reconciliation_widget": None,
    "account_accountant": None,       # absorbed / stubbed by bridge
    "documents": None,
    "ai": None,
    "iap_extract": None,
    "account_extract": None,
    "account_invoice_extract": None,
    "account_bank_statement_extract": None,
    "project_enterprise": None,
    "sale_external_tax": None,
    "stock_accountant": None,
    "account_fiscal_categories": None,
    "account_asset_fleet": None,
    "account_accountant_fleet": None,
    "accountant_knowledge": None,
    "account_online_synchronization": None,
    "account_peppol": None,
    "account_peppol_advanced_fields": None,
    "account_avatax": None,
    "account_avatax_sale": None,
    "account_avatax_stock": None,
    "account_avatax_geolocalize": None,
    "account_loans": None,
    "account_3way_match": None,
    "certificate": None,
    "base_vat": "base_vat",
}

# Never touch these keys when sanitising.
PROTECTED_MANIFEST_KEYS = {"name", "version", "summary", "description", "data", "demo", "assets"}

# Asset bundles that only exist when Enterprise modules are installed. Files
# declared under them must be re-homed into a bundle that Community loads.
ENTERPRISE_BUNDLE_MAP: Dict[str, str] = {
    "account_reports.assets_financial_report": "web.assets_backend",
    "account_accountant.assets_backend": "web.assets_backend",
    "account_reconciliation_widget.assets_backend": "web.assets_backend",
    "web_enterprise.assets_backend": "web.assets_backend",
    "mail_enterprise.assets_backend": "web.assets_backend",
    "point_of_sale.assets": "web.assets_backend",
}

TEXT_EXTENSIONS = (
    ".py", ".xml", ".js", ".scss", ".css", ".csv", ".po", ".pot",
    ".json", ".html", ".md", ".yml", ".yaml", ".txt",
)

SKIP_DIR_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "_toolkit", ".idea"}


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def iter_text_files(root: str) -> Iterable[str]:
    """Yield every text-ish file below *root*, skipping vendor/VCS folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS]
        for fn in filenames:
            if fn.endswith(TEXT_EXTENSIONS):
                yield os.path.join(dirpath, fn)


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Module index
# ---------------------------------------------------------------------------

@dataclass
class ModuleInfo:
    name: str
    path: str
    root_kind: str  # 'source' | 'core' | 'addons' | 'enterprise'
    manifest: Optional[dict] = None
    manifest_error: Optional[str] = None

    @property
    def depends(self) -> List[str]:
        if not self.manifest:
            return []
        deps = self.manifest.get("depends") or []
        return [d for d in deps if isinstance(d, str)]


class ModuleIndex:
    """Scan a set of addons roots and index every module directory."""

    def __init__(self, roots: Dict[str, str]):
        self.roots = dict(roots)
        self.modules: Dict[str, ModuleInfo] = {}
        self._scan()

    def _scan(self) -> None:
        for kind, root in self.roots.items():
            if not root or not os.path.isdir(root):
                continue
            for entry in sorted(os.listdir(root)):
                mpath = os.path.join(root, entry)
                mf = os.path.join(mpath, "__manifest__.py")
                if not os.path.isfile(mf):
                    continue
                # first root wins for duplicates, but always keep 'source' priority
                if entry in self.modules and kind != "source":
                    continue
                info = ModuleInfo(name=entry, path=mpath, root_kind=kind)
                try:
                    info.manifest = load_manifest(mf)
                except Exception as exc:  # noqa: BLE001
                    info.manifest_error = f"{type(exc).__name__}: {exc}"
                self.modules[entry] = info

    def has(self, name: str) -> bool:
        return name in self.modules

    def classify(self, dep: str) -> str:
        """Return one of: source, core, addons, enterprise, missing."""
        info = self.modules.get(dep)
        return info.root_kind if info else "missing"

    def manifest_of(self, name: str) -> Optional[dict]:
        info = self.modules.get(name)
        return info.manifest if info else None


# ---------------------------------------------------------------------------
# Manifest IO
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(?P<header>(?:[ \t]*#.*\n|[ \t]*\n)*)")


def load_manifest(path: str) -> dict:
    """Parse an Odoo manifest, tolerating comments / trailing commas / imports."""
    src = read_text(path)
    if src is None:
        raise OSError(f"cannot read {path}")
    try:
        return ast.literal_eval(src)
    except (ValueError, SyntaxError):
        pass
    # Fallback: execute in a sandbox with a restricted namespace.
    ns: Dict[str, object] = {}
    code = compile(src, path, "exec")
    exec(code, {"__builtins__": __builtins__}, ns)  # noqa: S102
    data = ns.get("MANIFEST", ns)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest did not evaluate to a dict")
    return data


def dump_manifest(data: dict, header: str = "", width: int = 110) -> str:
    """Deterministically serialise a manifest dict back to Python source."""
    body = pprint.pformat(dict(data), width=width, sort_dicts=False, indent=4)
    # `pprint` emits a leading dict render; keep it as-is (valid python literal).
    lines = body.splitlines()
    if header and not header.endswith("\n"):
        header += "\n"
    return f"{header}{body}\n"


def manifest_header(src: str) -> str:
    """Extract the leading comment/licence block that precedes the dict."""
    m = _HEADER_RE.match(src or "")
    return m.group("header") if m else ""


# ---------------------------------------------------------------------------
# Cross-reference scanning
# ---------------------------------------------------------------------------

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# Patterns that carry a "<module>.<xmlid>" reference.
XMLID_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("ref",        rf'(?:ref|inherit_id|t-call|t-call-assets|groups|domain|context|name)="({IDENT}\.{IDENT})"'),
    ("res_model",  rf'<field name="res_model">({IDENT})</field>'),
    ("model",      rf'<field name="model">({IDENT})</field>'),
    ("py_ref",     rf"env\.ref\(\s*['\"]({IDENT}\.{IDENT})['\"]"),
    ("py_inherit", rf"_inherit\s*=\s*['\"]({IDENT})['\"]"),
    ("py_model",   rf"model\s*=\s*['\"]({IDENT})['\"]"),
    ("csv_model",  rf"^{IDENT},({IDENT})"),
    ("asset_path", rf"['\"]({IDENT})/static/"),
    ("bundle",     rf"['\"]({IDENT})\.assets[_A-Za-z]*['\"]"),
)


@dataclass
class XrefHit:
    kind: str
    token: str
    module: str
    file: str
    line: int


def scan_xrefs(root: str) -> List[XrefHit]:
    """Collect all <module>.<identifier> references inside *root*."""
    hits: List[XrefHit] = []
    compiled = [(kind, re.compile(pat, re.MULTILINE)) for kind, pat in XMLID_PATTERNS]
    for path in iter_text_files(root):
        text = read_text(path)
        if text is None:
            continue
        for kind, rx in compiled:
            for m in rx.finditer(text):
                token = m.group(1)
                mod = token.split(".")[0]
                line = text.count("\n", 0, m.start()) + 1
                hits.append(XrefHit(kind, token, mod, path, line))
    return hits


# ---------------------------------------------------------------------------
# Brand icon generation (pure Pillow, with stdlib PNG fallback)
# ---------------------------------------------------------------------------

def _brand_pixels(size: int) -> Tuple[int, List[List[Tuple[int, int, int, int]]]]:
    """Build an RGBA pixel buffer for the msolutions mark."""
    # msolutions palette
    bg = (11, 61, 92, 255)        # deep petrol
    fg = (0, 199, 158, 255)       # msolutions teal
    accent = (255, 255, 255, 255)
    px = [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]
    radius = int(size * 0.22)
    for y in range(size):
        for x in range(size):
            # rounded square background
            cx = min(x, size - 1 - x)
            cy = min(y, size - 1 - y)
            inside = True
            if cx < radius and cy < radius:
                dx, dy = radius - cx, radius - cy
                inside = (dx * dx + dy * dy) <= radius * radius
            px[y][x] = bg if inside else (0, 0, 0, 0)
    # draw a stylised "M" with two vertical bars + valley
    bar_w = max(2, size // 12)
    top = int(size * 0.28)
    bot = int(size * 0.72)
    left = int(size * 0.22)
    right = int(size * 0.78)
    for y in range(top, bot):
        t = (y - top) / max(1, (bot - top - 1))
        for x in range(left, left + bar_w):
            px[y][x] = fg
        for x in range(right - bar_w, right):
            px[y][x] = fg
    mid = (left + right) // 2
    for y in range(top, bot):
        t = (y - top) / max(1, (bot - top - 1))
        x = int(mid + (0.5 - abs(0.5 - t)) * 0 - (1 - 2 * abs(0.5 - t)) * 0)
        # simple V valley
        off = int((1 - 2 * abs(t - 0.5)) * (right - left) * 0.18)
        x0 = mid - off
        x1 = mid + off
        for x in range(max(left, x0), min(right, x0 + bar_w)):
            px[y][x] = accent if (y - top) % 3 else fg
        _ = x1
    return size, px


def _write_png(path: str, size: int, pixels: List[List[Tuple[int, int, int, int]]]) -> None:
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)


def generate_icon(path: str, size: int = 128) -> str:
    """Create a branded PNG icon at *path*. Uses Pillow when available."""
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:  # noqa: BLE001
        _, px = _brand_pixels(size)
        _write_png(path, size, px)
        return path

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(11, 61, 92, 255))
    bar = max(2, size // 12)
    left, right = int(size * 0.22), int(size * 0.78)
    top, bot = int(size * 0.28), int(size * 0.72)
    teal = (0, 199, 158, 255)
    d.rectangle([left, top, left + bar, bot], fill=teal)
    d.rectangle([right - bar, top, right, bot], fill=teal)
    mid = (left + right) / 2
    span = (right - left) * 0.34
    d.polygon([(mid, bot), (mid - span, top + (bot - top) * 0.25),
               (mid - span + bar, top + (bot - top) * 0.25), (mid + 0, bot - (bot - top) * 0.3),
               (mid + span - bar, top + (bot - top) * 0.25), (mid + span, top + (bot - top) * 0.25)],
              fill=(255, 255, 255, 230))
    d.ellipse([size * 0.40, size * 0.40, size * 0.60, size * 0.60], outline=(255, 255, 255, 255), width=max(2, size // 48))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@dataclass
class Report:
    title: str
    sections: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, section: str, line: str) -> None:
        self.sections.setdefault(section, []).append(line)

    def render(self, stream=sys.stdout) -> None:
        stream.write(f"\n{'=' * 78}\n{self.title}\n{'=' * 78}\n")
        for section, lines in self.sections.items():
            stream.write(f"\n--- {section} ({len(lines)}) ---\n")
            for line in lines:
                stream.write(f"  {line}\n")
        stream.write("\n")

    def to_json(self, path: str) -> None:
        write_text(path, json.dumps({"title": self.title, "sections": self.sections}, indent=2))


def detect_workspace(source_dir: str) -> Dict[str, str]:
    """Derive the standard addons roots from a given source directory."""
    source_dir = os.path.abspath(source_dir)
    # .../msolutions_custom/accounting_ms -> odoo root is two levels up
    ms_custom = os.path.dirname(source_dir)
    odoo_root = os.path.dirname(ms_custom)
    roots = {
        "source": source_dir,
        "core": os.path.join(odoo_root, "odoo", "addons"),
        "addons": os.path.join(odoo_root, "addons"),
        "enterprise": os.path.join(odoo_root, "enterprise", "addons"),
        "enterprise_alt": os.path.join(ms_custom, "..", "custom", "addions_enterprise"),
    }
    return {k: os.path.normpath(v) for k, v in roots.items()}
