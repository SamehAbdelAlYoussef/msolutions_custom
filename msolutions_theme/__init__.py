# -*- coding: utf-8 -*-
import base64
import logging

_logger = logging.getLogger(__name__)

# Brass "balance & measure" mark + wordmark. Kept as SVG so it stays crisp at
# any size and adds no raster weight.
BRAND_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 60" fill="none">
  <g stroke="#B5853A" stroke-width="2.6" stroke-linecap="round">
    <path d="M12 14 V46 M12 20 H40" fill="none"/>
    <path d="M12 20 l-6 11 h12 z" fill="#B5853A" stroke="none"/>
    <circle cx="40" cy="20" r="3.1" fill="#143A2C" stroke="none"/>
  </g>
  <text x="58" y="27" font-family="'IBM Plex Sans','Segoe UI',system-ui,sans-serif"
        font-size="22" font-weight="600" letter-spacing="0.2" fill="#143A2C">Manager</text>
  <text x="58" y="48" font-family="'IBM Plex Sans','Segoe UI',system-ui,sans-serif"
        font-size="16" font-weight="400" letter-spacing="3.6" fill="#B5853A">SOLUTIONS</text>
</svg>"""


def post_init_hook(env):
    """Configure the Manager Solutions site: Arabic/RTL default, brand identity.

    Every step is independent and guarded so a single failure never aborts the
    module install. Safe to re-run.
    """
    # 1) Arabic as the site language (RTL direction comes from the lang record).
    try:
        env["res.lang"]._activate_lang("ar_001")
        _logger.info("msolutions_theme: activated Arabic (ar_001)")
    except Exception as e:  # noqa: BLE001
        _logger.warning("msolutions_theme: could not activate ar_001: %s", e)

    ar = env["res.lang"].search([("code", "=", "ar_001")], limit=1)

    # 2) Point every website at Arabic by default and make it available.
    for website in env["website"].search([]):
        try:
            if ar:
                langs = website.language_ids
                if ar not in langs:
                    website.language_ids = [(4, ar.id)]
                website.default_lang_id = ar.id
            website.name = "Manager Solutions"
        except Exception as e:  # noqa: BLE001
            _logger.warning("msolutions_theme: website config failed: %s", e)

    # 3) Company identity.
    try:
        company = env.company or env["res.company"].search([], limit=1)
        company.name = "Manager Solutions"
        company.logo = base64.b64encode(BRAND_SVG.encode("utf-8"))
        eg = env.ref("base.eg", raise_if_not_found=False)
        if eg:
            company.country_id = eg.id
    except Exception as e:  # noqa: BLE001
        _logger.warning("msolutions_theme: company config failed: %s", e)
