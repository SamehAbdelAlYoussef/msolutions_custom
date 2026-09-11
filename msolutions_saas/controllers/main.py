import json
import logging
import os
import zipfile

import werkzeug.wrappers

from odoo import fields
from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Streaming zip: stdlib zipfile writing to an UNSEEKABLE sink, so the archive is
# never held in memory or written to a temp file -- only ~64 KB chunks pass
# through. force_zip64=True on every file entry means the 4 GB boundary can never
# corrupt a large member (works on a 50 MB tenant AND a 20 GB one).
# ----------------------------------------------------------------------------
class _Sink:
    def __init__(self):
        self._buf = bytearray()
        self._pos = 0

    def write(self, b):
        self._buf += b
        self._pos += len(b)
        return len(b)

    def tell(self):
        return self._pos

    def flush(self):
        pass

    def seekable(self):
        return False

    def drain(self):
        if not self._buf:
            return b""
        d = bytes(self._buf)
        self._buf = bytearray()
        return d


def _zip_stream(entries):
    """entries: iterable of (arcname, "bytes"|"file", data_or_path)."""
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    for arcname, kind, payload in entries:
        if kind == "bytes":
            zf.writestr(arcname, payload)
            d = sink.drain()
            if d:
                yield d
            continue
        zi = zipfile.ZipInfo(arcname)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        with open(payload, "rb") as src, zf.open(zi, "w", force_zip64=True) as dst:
            while True:
                chunk = src.read(1 << 16)
                if not chunk:
                    break
                dst.write(chunk)
                d = sink.drain()
                if d:
                    yield d
        d = sink.drain()
        if d:
            yield d
    zf.close()
    d = sink.drain()
    if d:
        yield d


class SaasBackupDownloadController(Controller):

    def _rec(self, token):
        return request.env["saas.backup.download"]._by_token(token)

    def _valid(self, rec):
        return bool(rec) and rec.state != "expired" and not (
            rec.expires_at and rec.expires_at < fields.Datetime.now())

    # ---- the link the developer / customer opens ----
    @route("/saas/backup/download/<token>", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def page(self, token, **kw):
        rec = self._rec(token)
        if not self._valid(rec):
            return request.make_response(_PAGE_GONE, [("Content-Type", "text/html")])
        rec._activate()            # dormant customer link -> begin regeneration
        html = _PAGE_TMPL % {
            "tenant": _esc(rec.tenant_name),
            "as_of": _esc((rec.source_date and
                           rec.source_date.strftime("%Y-%m-%d %H:%M UTC")) or "now"),
            "expires": _esc((rec.expires_at and
                             rec.expires_at.strftime("%Y-%m-%d %H:%M UTC")) or ""),
            "token": _esc(token),
        }
        return request.make_response(html, [("Content-Type", "text/html; charset=utf-8")])

    # ---- polled by the page ----
    @route("/saas/backup/status/<token>", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def status(self, token, **kw):
        rec = self._rec(token)
        if not rec:
            return request.make_response(json.dumps({"state": "expired"}),
                                         [("Content-Type", "application/json")])
        return request.make_response(json.dumps(rec._status_dict()),
                                     [("Content-Type", "application/json")])

    # ---- the actual streamed zip (single-use) ----
    @route("/saas/backup/stream/<token>", type="http", auth="public",
           methods=["GET"], csrf=False, sitemap=False)
    def stream(self, token, **kw):
        rec = self._rec(token)
        if not self._valid(rec) or rec.state != "ready":
            return request.not_found()
        if not rec._claim_for_stream():        # atomic ready->streaming, single use
            return request.not_found()

        # Capture PLAIN values while the request env is still alive; the stream
        # generator below must not touch the ORM (its cursor is closed by then).
        staging = rec._staging_abs()
        sql_path = os.path.join(staging, "dump.sql")
        fs_dir = os.path.join(staging, "filestore")
        manifest_bytes = rec._manifest_json()
        readme_bytes = rec._readme_txt()
        fname = "%s_%s.zip" % (rec.tenant_name, (rec.source_date or
                               fields.Datetime.now()).strftime("%Y%m%d_%H%M"))

        def entries():
            yield ("manifest.json", "bytes", manifest_bytes)
            yield ("README.txt", "bytes", readme_bytes)
            yield ("dump.sql", "file", sql_path)
            for root, _dirs, files in os.walk(fs_dir):
                for f in sorted(files):
                    ap = os.path.join(root, f)
                    yield ("filestore/" + os.path.relpath(ap, fs_dir), "file", ap)

        counter = {"n": 0}

        def gen():
            for chunk in _zip_stream(entries()):
                counter["n"] += len(chunk)
                yield chunk
            # Odoo's WSGI does not reliably fire response close-hooks, so the
            # generator drops a marker when it is fully drained (the whole zip
            # reached the client). The prepare cron finalizes + cleans staging
            # from it; an aborted stream leaves no marker and is swept at expiry.
            try:
                with open(os.path.join(staging, ".stream_done"), "w") as fh:
                    fh.write(str(counter["n"]))
            except Exception:  # noqa: BLE001
                pass

        headers = [
            ("Content-Type", "application/zip"),
            ("Content-Disposition", 'attachment; filename="%s"' % fname),
            ("X-Content-Type-Options", "nosniff"),
            ("Cache-Control", "no-store"),
        ]
        return werkzeug.wrappers.Response(gen(), headers=headers,
                                          direct_passthrough=True)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_PAGE_GONE = ("<!doctype html><meta charset='utf-8'><title>Link expired</title>"
              "<body style='font-family:system-ui;max-width:36rem;margin:4rem auto;"
              "text-align:center'><h1>This link has expired</h1>"
              "<p>Backup download links are single-use and time-limited. "
              "Ask for a new one.</p></body>")

# Self-contained page: shows the backup's own timestamp, a real state + ETA, and
# tells the visitor they can close it and come back -- the link stays valid until
# it is used. Polls /status; when ready it navigates to /stream (the download).
_PAGE_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preparing your backup</title>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:38rem;
   margin:3rem auto;padding:0 1rem;color:#0f172a}
 .card{border:1px solid #e2e8f0;border-radius:14px;padding:1.6rem 1.8rem;box-shadow:0 10px 30px -18px rgba(0,0,0,.3)}
 h1{font-size:1.25rem;margin:.2rem 0 1rem} .muted{color:#64748b;font-size:.92rem}
 .row{margin:.5rem 0} code{background:#f1f5f9;padding:.1rem .35rem;border-radius:6px}
 .bar{height:8px;background:#e2e8f0;border-radius:99px;overflow:hidden;margin:1rem 0}
 .bar>i{display:block;height:100%%;width:35%%;background:#2563eb;border-radius:99px;
   animation:slide 1.3s ease-in-out infinite}
 @keyframes slide{0%%{margin-left:-35%%}100%%{margin-left:100%%}}
 .btn{display:inline-block;margin-top:1rem;background:#2563eb;color:#fff;text-decoration:none;
   padding:.7rem 1.2rem;border-radius:10px;font-weight:600}
 .err{color:#b91c1c}
</style></head><body><div class="card">
 <h1>Preparing the backup for <code>%(tenant)s</code></h1>
 <div class="row muted">Data as of <b>%(as_of)s</b> (this is the backup's own time, not now).</div>
 <div id="stat" class="row">Starting&hellip;</div>
 <div class="bar" id="bar"><i></i></div>
 <div class="row muted" id="eta"></div>
 <div class="row muted">Large datasets can take several minutes. You can close this
   page and reopen the same link later &mdash; it stays valid until %(expires)s or
   until the download completes.</div>
 <div id="act"></div>
</div>
<script>
var token=%(token)r;
function human(s){s=Math.max(0,s|0);if(s<60)return s+"s";var m=Math.round(s/60);return "about "+m+" min";}
function poll(){
 fetch("/saas/backup/status/"+token).then(function(r){return r.json();}).then(function(d){
  var stat=document.getElementById("stat"),eta=document.getElementById("eta"),
      bar=document.getElementById("bar"),act=document.getElementById("act");
  if(d.ready){ stat.textContent="Ready. Your download is starting…";
   bar.style.display="none"; eta.textContent="";
   act.innerHTML='<a class="btn" href="/saas/backup/stream/'+token+'">Download now</a>';
   window.location="/saas/backup/stream/"+token; return; }
  if(d.done){ stat.textContent="This link has already been used."; bar.style.display="none"; return; }
  if(d.failed){ stat.className="row err";
   stat.textContent="Preparation failed"+(d.error?": "+d.error:"")+"."; bar.style.display="none"; return; }
  stat.textContent = d.state==="preparing" ? "Assembling the backup…"
                    : d.state==="pending" ? "Queued…" : "Working…";
  eta.textContent = d.eta_seconds ? "Estimated: "+human(d.eta_seconds) : "";
  setTimeout(poll, 4000);
 }).catch(function(){ setTimeout(poll, 6000); });
}
poll();
</script></body></html>"""
