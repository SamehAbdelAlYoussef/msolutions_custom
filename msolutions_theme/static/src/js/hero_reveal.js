/* Manager Solutions — hero line-draw + on-scroll reveals.
   No library. All motion is gated on prefers-reduced-motion, and the reveal
   hidden-state is applied via the .ms-anim class (added here) so content stays
   visible if JS never runs. */
(function () {
    "use strict";
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function drawHero() {
        var art = document.querySelector(".ms-hero-art");
        if (!art || reduce) return;
        var paths = art.querySelectorAll("[data-draw]");
        Array.prototype.forEach.call(paths, function (p, i) {
            var len = p.getTotalLength ? p.getTotalLength() : 0;
            if (!len) return;
            p.style.strokeDasharray = len;
            p.style.strokeDashoffset = len;
            p.style.transition = "stroke-dashoffset 1.1s cubic-bezier(.22,.61,.36,1) " + (0.15 + i * 0.12) + "s";
            requestAnimationFrame(function () {
                requestAnimationFrame(function () { p.style.strokeDashoffset = "0"; });
            });
        });
    }

    function initReveal() {
        if (reduce || !("IntersectionObserver" in window)) return;
        var els = document.querySelectorAll(".ms-reveal, .ms-stagger, .ms-focus");
        if (!els.length) return;
        document.documentElement.classList.add("ms-anim");
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) {
                if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
            });
        }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
        Array.prototype.forEach.call(els, function (el) { io.observe(el); });
    }

    function brandContact() {
        // Replace Odoo's placeholder header phone/email with the real ones.
        var TEL = "+201131316911", TEL_DISP = "01131316911", EMAIL = "info@msolutions-eg.com";
        Array.prototype.forEach.call(document.querySelectorAll('a[href="tel:+1 555-555-5556"]'), function (a) {
            a.setAttribute("href", "tel:" + TEL);
            var t = a.querySelector(".o_force_ltr small") || a.querySelector(".o_force_ltr") || a.querySelector("small");
            if (t) { t.textContent = TEL_DISP; } else { a.textContent = TEL_DISP; }
        });
        Array.prototype.forEach.call(document.querySelectorAll('a[href^="mailto:info@yourcompany"]'), function (a) {
            a.setAttribute("href", "mailto:" + EMAIL);
            var t = a.querySelector(".o_force_ltr small") || a.querySelector(".o_force_ltr") || a.querySelector("small");
            if (t) { t.textContent = EMAIL; } else { a.textContent = EMAIL; }
        });
    }

    function initVideos() {
        var cards = document.querySelectorAll(".ms-video");
        if (!cards.length) return;
        var vids = document.querySelectorAll(".ms-video-el");
        // Autoplay muted while in view; pause when out (saves resources).
        if ("IntersectionObserver" in window) {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) {
                    if (e.isIntersecting) { e.target.play().catch(function () {}); }
                    else { e.target.pause(); }
                });
            }, { threshold: 0.35 });
            Array.prototype.forEach.call(vids, function (v) { io.observe(v); });
        }
        // Tap the button to turn sound on (and ensure only one plays with sound).
        Array.prototype.forEach.call(cards, function (card) {
            var v = card.querySelector(".ms-video-el");
            var btn = card.querySelector(".ms-video-sound");
            if (!v || !btn) return;
            btn.addEventListener("click", function () {
                if (v.muted) {
                    Array.prototype.forEach.call(vids, function (o) { if (o !== v) { o.muted = true; } });
                    Array.prototype.forEach.call(document.querySelectorAll(".ms-video.is-playing"), function (c) { c.classList.remove("is-playing"); });
                    v.muted = false;
                    v.play().catch(function () {});
                    card.classList.add("is-playing");
                } else {
                    v.muted = true;
                    card.classList.remove("is-playing");
                }
            });
        });
    }

    function rpc(url, params) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params || {} }),
        }).then(function (r) { return r.json(); }).then(function (j) { return j.result || {}; });
    }

    function initSignup() {
        var wiz = document.getElementById("ms-signup");
        if (!wiz) return;
        var form = wiz.querySelector(".ms-signup-form");
        var result = wiz.querySelector(".ms-signup-result");
        var pkgLabel = wiz.querySelector(".ms-signup-pkg");
        var pkgInput = form.querySelector("[name=package]");
        var msg = wiz.querySelector(".ms-signup-msg");
        var go = form.querySelector(".ms-signup-go");
        var company = form.querySelector("[name=company]");
        var sub = form.querySelector("[name=subdomain]");
        var subEdited = false;

        var sheet = wiz.querySelector(".ms-signup-sheet");
        function openSheet(pkg) {
            pkgLabel.textContent = pkg || "";
            pkgInput.value = pkg || "";
            result.hidden = true;
            form.hidden = false;
            wiz.hidden = false;
            wiz.setAttribute("aria-hidden", "false");
            if (sheet) sheet.scrollTop = 0;       // always open at the top
            void wiz.offsetWidth;                 // reflow so the slide-up transition runs
            wiz.classList.add("is-open");
            document.documentElement.style.overflow = "hidden";
            // focus without scrolling the sheet (which pushed mobile to mid-form)
            setTimeout(function () {
                if (sheet) sheet.scrollTop = 0;
                try { company.focus({ preventScroll: true }); } catch (_e) { /* no focus if unsupported */ }
            }, 460);
        }
        function closeSheet() {
            wiz.classList.remove("is-open");
            wiz.setAttribute("aria-hidden", "true");
            document.documentElement.style.overflow = "";
            setTimeout(function () { wiz.hidden = true; }, 450);
        }

        // The whole plan card opens the wizard (not just the button).
        Array.prototype.forEach.call(document.querySelectorAll(".ms-plan"), function (card) {
            card.addEventListener("click", function (e) {
                e.preventDefault();
                var cta = card.querySelector(".ms-plan-cta");
                openSheet(cta ? cta.getAttribute("data-package") : "");
            });
        });
        // Header + hero "start in seconds" links open the wizard on the recommended plan.
        Array.prototype.forEach.call(document.querySelectorAll('a[href$="#ms-start"]'), function (a) {
            a.addEventListener("click", function (e) {
                e.preventDefault();
                openSheet("الباقة المتقدّمة");
            });
        });
        Array.prototype.forEach.call(wiz.querySelectorAll("[data-close]"), function (el) {
            el.addEventListener("click", closeSheet);
        });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && !wiz.hidden) closeSheet();
        });

        sub.addEventListener("input", function () { subEdited = true; });
        company.addEventListener("input", function () {
            if (subEdited) return;
            sub.value = (company.value || "").toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 30);
        });

        form.addEventListener("submit", function (e) {
            e.preventDefault();
            msg.textContent = "";
            var data = {};
            ["company", "activity", "subdomain", "email", "phone", "package", "company_url"].forEach(function (n) {
                var el = form.querySelector("[name=" + n + "]");
                data[n] = el ? el.value : "";
            });
            go.disabled = true; go.textContent = "جاري الإنشاء…";
            rpc("/ms/signup", data).then(function (r) {
                if (!r.ok) { msg.textContent = r.error || "حدث خطأ."; go.disabled = false; go.textContent = "ابدأ الإنشاء"; return; }
                poll(0);
            }).catch(function () { msg.textContent = "تعذّر الاتصال، حاول مرة أخرى."; go.disabled = false; go.textContent = "ابدأ الإنشاء"; });
        });

        function poll(n) {
            rpc("/ms/signup/status", {}).then(function (r) {
                if (r.state === "active") { showResult(r); return; }
                if (r.state === "error") { msg.textContent = r.error || "تعذّر إكمال الإنشاء."; go.disabled = false; go.textContent = "ابدأ الإنشاء"; return; }
                if (n > 40) { msg.textContent = "الإنشاء يستغرق وقتًا أطول من المعتاد، سنُعلمك قريبًا."; go.disabled = false; go.textContent = "ابدأ الإنشاء"; return; }
                setTimeout(function () { poll(n + 1); }, 2000);
            }).catch(function () { setTimeout(function () { poll(n + 1); }, 2500); });
        }

        function showResult(r) {
            form.hidden = true;
            result.hidden = false;
            var u = result.querySelector(".ms-res-url");
            u.textContent = r.url; u.href = r.url;
            result.querySelector(".ms-res-login").textContent = r.login;
            // Never print a falsy password: textContent would render the
            // literal "false" to the customer (2026-10-20).
            result.querySelector(".ms-res-pass").textContent =
                r.password || "— اطلبها من الدعم —";
            result.querySelector(".ms-res-open").href = r.url;
            result.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    // Odoo overview: plays by itself once the section is on screen and pauses
    // when it leaves. Autoplay is only permitted while muted, so it starts
    // muted and the corner button turns the sound on -- same contract as the
    // TikTok cards above. preload="none" means nothing is fetched until the
    // visitor actually reaches the section.
    function initOdooVideo() {
        var wrap = document.querySelector(".ms-odoo-player");
        if (!wrap) return;
        var v = wrap.querySelector(".ms-odoo-video");
        var btn = wrap.querySelector(".ms-odoo-sound");
        if (!v) return;

        var inView = false;      // so a late user gesture only unmutes on screen
        var forcedMute = false;  // true = muted because the browser refused sound

        // Browsers reject play() with sound unless the page already has user
        // activation, and scrolling does NOT grant it. So: ask for sound, and
        // fall back to muted rather than not playing at all.
        function playPreferSound() {
            v.muted = false;
            return v.play().then(function () {
                forcedMute = false;
                wrap.classList.add("is-live", "is-unmuted");
            }).catch(function () {
                v.muted = true;
                forcedMute = true;
                wrap.classList.remove("is-unmuted");
                return v.play().then(function () { wrap.classList.add("is-live"); })
                               .catch(function () {});
            });
        }

        if ("IntersectionObserver" in window) {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) {
                    inView = e.isIntersecting;
                    if (inView) { playPreferSound(); } else { v.pause(); }
                });
            }, { threshold: 0.35 });
            io.observe(v);

            // First real gesture anywhere grants activation for the rest of the
            // visit; if we are still only muted because sound was refused, take
            // it back. Once, and only while the section is actually on screen.
            var claim = function () {
                if (inView && forcedMute && v.muted) {
                    forcedMute = false;
                    v.muted = false;
                    wrap.classList.add("is-unmuted");
                    v.play().catch(function () {});
                }
                document.removeEventListener("pointerdown", claim, true);
                document.removeEventListener("keydown", claim, true);
            };
            document.addEventListener("pointerdown", claim, true);
            document.addEventListener("keydown", claim, true);
        } else {
            v.controls = true;   // no observer: let the visitor drive it
            wrap.classList.add("is-live");
        }

        if (!btn) return;
        btn.addEventListener("click", function () {
            if (v.muted) {
                // Only one thing with sound at a time -- mute the TikTok cards.
                Array.prototype.forEach.call(document.querySelectorAll(".ms-video-el"), function (o) { o.muted = true; });
                Array.prototype.forEach.call(document.querySelectorAll(".ms-video.is-playing"), function (c) { c.classList.remove("is-playing"); });
                v.muted = false;
                wrap.classList.add("is-unmuted");
                v.play().catch(function () {});
            } else {
                v.muted = true;
                wrap.classList.remove("is-unmuted");
            }
        });
    }

    function init() { drawHero(); initReveal(); brandContact(); initVideos(); initOdooVideo(); initSignup(); }
    if (document.readyState !== "loading") init();
    else document.addEventListener("DOMContentLoaded", init);
})();
