# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ThemeUtils(models.AbstractModel):
    """Setup run by Odoo each time the Manager Solutions theme is applied
    from the website "Switch Theme" gallery."""

    _inherit = "theme.utils"

    def _theme_msolutions_post_copy(self, mod):
        # Brand chrome: standard header + the inherited (restyled) footer.
        self.enable_view("website.template_header_default")
        self.enable_view("website.footer_custom")

        # Clear any editor/Studio snippets off the homepage so the theme's own
        # landing design shows on a clean slate: drop the per-website copy of the
        # homepage view and let it fall back to this theme's template.
        try:
            website = self.env["website"].get_current_website()
            cow = self.env["ir.ui.view"].search([
                ("key", "=", "website.homepage"),
                ("website_id", "=", website.id),
            ])
            if cow:
                cow.unlink()
                _logger.info("msolutions_theme: reset homepage (removed %s editor copies)", len(cow))
        except Exception as e:  # noqa: BLE001
            _logger.warning("msolutions_theme: homepage reset in post_copy failed: %s", e)
