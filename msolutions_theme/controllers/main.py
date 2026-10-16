# -*- coding: utf-8 -*-
"""Public self-service signup → provisions a SaaS tenant.

The web tier (odoo_web) cannot create databases by design; it only queues the
tenant (state='provisioning') and nudges the provisioning cron, which runs in
the worker as odoo_provision and clones the plan template in ~1s. The browser
then polls /ms/signup/status until the tenant is active and shows the URL +
one-time credentials (guarded to the session that created it).

Abuse protection (per the owner's choice: rate-limit + simple guard):
  * honeypot field,
  * in-memory per-IP cooldown,
  * a global cap on concurrent provisions,
  * one pending signup per browser session.
"""
import logging
import re
import time

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Same rule as saas.tenant: a db name AND a DNS label.
_NAME_RE = re.compile(r"^[a-z][a-z0-9]{2,30}$")
_RESERVED = {
    "postgres", "template0", "template1", "odoo", "root",
    "www", "mail", "smtp", "imap", "webmail", "ns1", "ns2",
    "api", "app", "cdn", "static", "assets",
    "admin", "manage", "saas", "msolutions",
}

_IP_COOLDOWN_S = 300          # one signup per IP per 5 minutes
_MAX_CONCURRENT_PROVISION = 3  # global safety cap
_last_signup_by_ip = {}        # in-memory; per-worker, resets on restart (fine)


def _err(msg):
    return {"ok": False, "error": msg}


class MsSignup(http.Controller):

    @http.route("/ms/signup", type="json", auth="public", methods=["POST"], website=True)
    def signup(self, **kw):
        # 1) Honeypot — real users never fill this hidden field.
        if (kw.get("company_url") or "").strip():
            return _err("تعذّر إرسال الطلب.")

        company = (kw.get("company") or "").strip()
        activity = (kw.get("activity") or "").strip()
        subdomain = (kw.get("subdomain") or "").strip().lower()
        email = (kw.get("email") or "").strip()
        phone = (kw.get("phone") or "").strip()
        package = (kw.get("package") or "").strip()

        if not company:
            return _err("من فضلك اكتب اسم الشركة.")
        if not _NAME_RE.match(subdomain):
            return _err("العنوان الفرعي: حروف إنجليزية صغيرة وأرقام فقط، يبدأ بحرف (3–31 خانة).")
        if subdomain in _RESERVED:
            return _err("هذا العنوان الفرعي محجوز، اختر غيره.")

        # 2) Per-IP cooldown.
        ip = request.httprequest.remote_addr or "?"
        now = time.time()
        if now - _last_signup_by_ip.get(ip, 0) < _IP_COOLDOWN_S:
            return _err("لقد أرسلت طلبًا منذ قليل، برجاء المحاولة بعد بضع دقائق.")

        Tenant = request.env["saas.tenant"].sudo()

        # 3) Global concurrency cap.
        if Tenant.search_count([("state", "=", "provisioning")]) >= _MAX_CONCURRENT_PROVISION:
            return _err("النظام يُنشئ حسابات أخرى الآن، برجاء المحاولة بعد دقيقة.")

        # 4) Uniqueness (tenant record + an actual database of that name).
        if Tenant.search_count([("name", "=", subdomain)]):
            return _err("العنوان الفرعي مستخدم بالفعل، اختر غيره.")

        plan = request.env.ref("msolutions_saas.plan_basic", raise_if_not_found=False)
        try:
            tenant = Tenant.create({
                "name": subdomain,
                "company_name": company,
                "plan_id": plan.id if plan else False,
            })
            if tenant._database_exists():
                tenant.unlink()
                return _err("يوجد حساب بنفس هذا العنوان بالفعل، اختر غيره.")
            # Queue it (same effect as action_provision, without the dev-group gate).
            tenant.write({
                "state": "provisioning",
                "error_message": False,
                "state_since": fields.Datetime.now(),
            })
            tenant._log(
                "provision_queued",
                "public signup — company=%s | activity=%s | email=%s | phone=%s | package=%s | ip=%s"
                % (company, activity, email, phone, package, ip),
            )
            request.env.cr.commit()  # make it visible to the worker before we nudge it
        except Exception as e:  # noqa: BLE001
            _logger.exception("public signup failed")
            request.env.cr.rollback()
            return _err("حدث خطأ أثناء إنشاء الحساب، برجاء المحاولة لاحقًا.")

        # 5) Nudge the provisioning cron so it runs within seconds, not up to 1 min.
        try:
            cron = request.env.ref("msolutions_saas.cron_process_tenants", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        except Exception:  # noqa: BLE001
            pass  # the periodic cron will still pick it up

        _last_signup_by_ip[ip] = now
        request.session["ms_tenant_id"] = tenant.id
        return {"ok": True, "subdomain": subdomain}

    @http.route("/ms/signup/status", type="json", auth="public", methods=["POST"], website=True)
    def status(self, **kw):
        tid = request.session.get("ms_tenant_id")
        if not tid:
            return _err("لا توجد عملية إنشاء جارية.")
        t = request.env["saas.tenant"].sudo().browse(tid)
        if not t.exists():
            return _err("تعذّر العثور على الحساب.")
        if t.state == "active":
            # One-time reveal to the creating session only.
            return {
                "ok": True, "state": "active",
                "url": t.url, "login": t.admin_login, "password": t.admin_password,
            }
        if t.state == "error":
            return {"ok": True, "state": "error",
                    "error": t.error_message or "تعذّر إكمال الإنشاء."}
        return {"ok": True, "state": t.state}
