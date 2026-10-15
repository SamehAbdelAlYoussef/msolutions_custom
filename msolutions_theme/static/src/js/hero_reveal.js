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
        var els = document.querySelectorAll(".ms-reveal, .ms-stagger");
        if (!els.length) return;
        document.documentElement.classList.add("ms-anim");
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) {
                if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
            });
        }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
        Array.prototype.forEach.call(els, function (el) { io.observe(el); });
    }

    function init() { drawHero(); initReveal(); }
    if (document.readyState !== "loading") init();
    else document.addEventListener("DOMContentLoaded", init);
})();
