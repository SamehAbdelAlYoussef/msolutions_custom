# -*- coding: utf-8 -*-
{
    "name": "Manager Solutions — Website Theme",
    "summary": "Arabic-first landing page and brand theme for Manager Solutions",
    "description": """
Bespoke website theme for Manager Solutions (msolutions-eg.com):
a distinctive Arabic/RTL landing page, a redesigned navbar and footer built by
*inheriting* the standard website templates (never replacing them, so the
website editor keeps working), self-hosted fonts, and SEO metadata.
""",
    "version": "19.0.1.0.0",
    "category": "Website/Theme",
    "author": "Manager Solutions",
    "website": "https://msolutions-eg.com",
    "license": "LGPL-3",
    "depends": ["website"],
    "data": [
        "data/website_menu.xml",
        "views/footer.xml",
        "views/homepage.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "msolutions_theme/static/src/scss/fonts.scss",
            "msolutions_theme/static/src/scss/theme.scss",
            "msolutions_theme/static/src/js/hero_reveal.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
