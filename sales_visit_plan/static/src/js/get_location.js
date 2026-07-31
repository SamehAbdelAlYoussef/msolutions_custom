/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { _t } from "@web/core/l10n/translation";
import { onMounted } from "@odoo/owl";

/**
 * Client-side geolocation button handler.
 *
 * In Odoo 19, button clicks go through env.onClickViewButton → RPC.
 * The env is frozen (useSubEnv + Object.freeze), so we can't wrap
 * onClickViewButton.  Instead we install a native DOM "capture" listener
 * that fires before Owl's synthetic events and short-circuits the button.
 */
patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        const self = this;
        onMounted(() => {
            const el = self.rootRef.el;
            if (!el) {
                return;
            }
            el.addEventListener(
                "click",
                (e) => {
                    const btn = e.target.closest('button[name="onClickGetLocation"]');
                    if (!btn) {
                        return;
                    }
                    e.stopImmediatePropagation();
                    e.preventDefault();
                    self._getBrowserLocation();
                },
                true // capture phase — runs before Owl's synthetic handler
            );
        });
    },

    // ----------------------------------------------------------------
    // Geolocation → Google Maps URL → fill form fields
    // ----------------------------------------------------------------
    async _getBrowserLocation() {
        const record = this.model.root;
        if (!record) {
            return;
        }

        const notification = this.env.services.notification;

        if (!navigator.geolocation) {
            notification.add(
                _t("Geolocation is not supported by your browser."),
                { type: "danger" }
            );
            return;
        }

        notification.add(
            _t("Getting your current location…"),
            { type: "info" }
        );

        try {
            const pos = await new Promise((resolve, reject) => {
                navigator.geolocation.getCurrentPosition(
                    resolve,
                    reject,
                    {
                        enableHighAccuracy: true,
                        timeout: 15000,
                        maximumAge: 0,
                    }
                );
            });

            const lat = pos.coords.latitude.toFixed(7);
            const lng = pos.coords.longitude.toFixed(7);
            const url = `https://www.google.com/maps?q=${lat},${lng}`;

            // Write all three fields at once (framework fires onchange)
            await record.update({
                location_url: url,
                partner_latitude: parseFloat(lat),
                partner_longitude: parseFloat(lng),
            });

            notification.add(
                _t("📍 Location captured!  %(lat)s , %(lng)s",
                   { lat, lng }),
                { type: "success" }
            );
        } catch (err) {
            let msg = _t("Could not get your location.");
            if (err && err.code === 1) {
                msg = _t(
                    "Location permission denied. Please allow location "
                    + "access in your browser settings and try again."
                );
            } else if (err && err.code === 2) {
                msg = _t("Location unavailable. Check your deviceʼs GPS.");
            } else if (err && err.code === 3) {
                msg = _t("Location request timed out. Please try again.");
            }
            notification.add(msg, { type: "danger" });
        }
    },
});
