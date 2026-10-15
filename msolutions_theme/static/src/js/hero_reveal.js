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

    function init() { drawHero(); initReveal(); brandContact(); initVideos(); }
    if (document.readyState !== "loading") init();
    else document.addEventListener("DOMContentLoaded", init);
})();
