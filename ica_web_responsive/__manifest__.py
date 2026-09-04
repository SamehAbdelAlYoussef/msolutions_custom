# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'ICA Web Responsive',
    'author': 'Agga, IdeaCode Academy',
    'version': '19.0.1.0',
    'depends': ['web', 'base_setup'],
    'auto_install': False,
    'data': [
        'views/webclient_templates.xml',
    ],
    'assets': {
        'web._assets_primary_variables': [
            ('after', 'web/static/src/scss/primary_variables.scss', 'ica_web_responsive/static/src/**/*.variables.scss'),
            ('before', 'web/static/src/scss/primary_variables.scss', 'ica_web_responsive/static/src/scss/primary_variables.scss'),
        ],
        'web._assets_secondary_variables': [
            ('before', 'web/static/src/scss/secondary_variables.scss', 'ica_web_responsive/static/src/scss/secondary_variables.scss'),
        ],
        'web._assets_backend_helpers': [
            ('before', 'web/static/src/scss/bootstrap_overridden.scss', 'ica_web_responsive/static/src/scss/bootstrap_overridden.scss'),
        ],
        'web.assets_frontend': [
            'ica_web_responsive/static/src/webclient/home_menu/home_menu_background.scss',
            'ica_web_responsive/static/src/webclient/navbar/navbar.scss',
        ],
        'web.assets_backend': [
            'ica_web_responsive/static/src/webclient/**/*.scss',
            'ica_web_responsive/static/src/views/**/*.scss',

            'ica_web_responsive/static/src/core/**/*',
            'ica_web_responsive/static/src/webclient/**/*.js',
            ('after', 'web/static/src/views/list/list_renderer.xml', 'ica_web_responsive/static/src/views/list/list_renderer_desktop.xml'),
            'ica_web_responsive/static/src/webclient/**/*.xml',
            'ica_web_responsive/static/src/views/**/*.js',
            'ica_web_responsive/static/src/views/**/*.xml',
            ('remove', 'ica_web_responsive/static/src/views/pivot/**'),

            # Don't include dark mode files in light mode
            ('remove', 'ica_web_responsive/static/src/**/*.dark.scss'),
        ],
        'web.assets_backend_lazy': [
            'ica_web_responsive/static/src/views/pivot/**',
        ],
        'web.assets_backend_lazy_dark': [
            ('include', 'web.assets_backend_lazy'),
        ],
        'web.assets_web': [
            ('replace', 'web/static/src/main.js', 'ica_web_responsive/static/src/main.js'),
        ],
        # ========= Dark Mode =========
        # Strategy for Odoo 19: dark variable files are listed FIRST (before the
        # web.assets_web include) so that !default assignments in primary_variables.scss
        # do not override the already-set dark values.
        "web.assets_web_dark": [
            # 1. Dark variable overrides — must come before web.assets_web so that
            #    Bootstrap _variables.scss (compiled inside web.assets_web) picks up
            #    the dark values instead of the light !default ones.
            'ica_web_responsive/static/src/scss/primary_variables.dark.scss',
            'ica_web_responsive/static/src/**/*.variables.dark.scss',
            'ica_web_responsive/static/src/scss/secondary_variables.dark.scss',
            # 2. Full web bundle (light !default values won't override step 1)
            ('include', 'web.assets_web'),
            # 3. Bootstrap function overrides for dark mode (tint/shade swap).
            #    Must come after _functions.scss (inside web.assets_web) so the
            #    SCSS compiler uses our version for subsequent dark component files.
            'ica_web_responsive/static/src/scss/bs_functions_overridden.dark.scss',
            # 4. Dark component overrides — the wildcard also picks up variable/
            #    secondary files already added above, so remove the duplicates.
            'ica_web_responsive/static/src/**/*.dark.scss',
            ('remove', 'ica_web_responsive/static/src/scss/primary_variables.dark.scss'),
            ('remove', 'ica_web_responsive/static/src/**/*.variables.dark.scss'),
            ('remove', 'ica_web_responsive/static/src/scss/secondary_variables.dark.scss'),
            ('remove', 'ica_web_responsive/static/src/scss/bs_functions_overridden.dark.scss'),
        ],
    },
    'license': 'LGPL-3',

    'images': ['static/description/img.png'],
}
