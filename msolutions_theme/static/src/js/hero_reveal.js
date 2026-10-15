/* Manager Solutions — hero line-draw.
   One orchestrated reveal on load. No library, no per-section effects. */
(function () {
    "use strict";
    function draw() {
        var art = document.querySelector(".ms-hero-art");
        if (!art) return;
        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        var paths = art.querySelectorAll("[data-draw]");
        Array.prototype.forEach.call(paths, function (p, i) {
            var len = p.getTotalLength ? p.getTotalLength() : 0;
            if (!len) return;
            p.style.strokeDasharray = len;
            p.style.strokeDashoffset = len;
            p.style.transition = "stroke-dashoffset 1.1s cubic-bezier(.22,.61,.36,1) " + (0.15 + i * 0.12) + "s";
            // next frame → animate to 0
            requestAnimationFrame(function () {
                requestAnimationFrame(function () { p.style.strokeDashoffset = "0"; });
            });
        });
    }
    if (document.readyState !== "loading") draw();
    else document.addEventListener("DOMContentLoaded", draw);
})();
