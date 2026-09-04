/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PivotRenderer } from "@web/views/pivot/pivot_renderer";

import { useEffect, useRef } from "@odoo/owl";

// getPadding was removed in Odoo 19; padding is now inline in the XML template.
// We only keep the mobile tooltip-strip patch.
patch(PivotRenderer.prototype, {
    setup() {
        super.setup();
        this.root = useRef("root");
        if (this.env.isSmall) {
            useEffect(() => {
                if (this.root.el) {
                    const tooltipElems = this.root.el.querySelectorAll("*[data-tooltip]");
                    for (const el of tooltipElems) {
                        el.removeAttribute("data-tooltip");
                        el.removeAttribute("data-tooltip-position");
                    }
                }
            });
        }
    },
});
