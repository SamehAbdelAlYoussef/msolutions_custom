/* Trial warning bar. nginx injects window.MS_TRIAL_DAYS (days left) and this
 * script into trial tenants' pages only. Bottom bar so it never covers the
 * Odoo navbar; dismissible for the current browser session. */
(function () {
  var d = window.MS_TRIAL_DAYS;
  if (d === undefined || d === "" || document.getElementById("ms-trial-bar")) return;
  try { if (sessionStorage.getItem("ms_trial_dismissed") === String(d)) return; } catch (e) {}
  d = parseInt(d, 10);
  if (isNaN(d)) return;
  var urgent = d <= 3;
  var msg = d <= 0
    ? "الفترة التجريبية تنتهي اليوم"
    : "الفترة التجريبية تنتهي خلال " + d + (d === 1 ? " يوم" : " أيام");
  var bar = document.createElement("div");
  bar.id = "ms-trial-bar";
  bar.setAttribute("dir", "rtl");
  bar.style.cssText = "position:fixed;bottom:0;left:0;right:0;z-index:2147483000;display:flex;" +
    "align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;padding:10px 14px;" +
    "font:14px Tahoma,Arial,sans-serif;color:#fff;box-shadow:0 -2px 10px rgba(0,0,0,.25);" +
    "background:" + (urgent ? "#c0392b" : "#122b44");
  bar.innerHTML = "<span>⏳ " + msg + " — للاشتراك وتفعيل حسابك تواصل معنا</span>" +
    "<a href='https://msolutions-eg.com/' target='_blank' rel='noopener' " +
    "style='background:#fff;color:#122b44;padding:5px 14px;border-radius:6px;text-decoration:none;font-weight:600'>تواصل معنا</a>" +
    "<button type='button' aria-label='إغلاق' style='background:none;border:0;color:#fff;font-size:18px;cursor:pointer'>✕</button>";
  bar.querySelector("button").onclick = function () {
    try { sessionStorage.setItem("ms_trial_dismissed", String(d)); } catch (e) {}
    bar.remove();
  };
  document.body.appendChild(bar);
})();
