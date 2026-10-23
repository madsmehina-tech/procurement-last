/* v25.3 - three small behaviours, no libraries.
   1) phone menu button  2) rows that open a page by click also work with the keyboard  3) a form cannot be sent twice by a double click */
(function () {
  "use strict";

  var toggle = document.querySelector(".nav-toggle"), side = document.getElementById("sidebar");
  if (toggle && side) {
    toggle.addEventListener("click", function () {
      var open = side.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // rows with onclick="location.href='...'" -> real keyboard-reachable links
  document.querySelectorAll("tr[onclick]").forEach(function (tr) {
    var m = /location\.href\s*=\s*'([^']+)'/.exec(tr.getAttribute("onclick") || "");
    if (!m) return;
    tr.setAttribute("data-href", m[1]);
    tr.setAttribute("tabindex", "0");
    tr.setAttribute("role", "link");
    tr.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target === tr) { location.href = m[1]; }
    });
  });

  // double-submit protection: after a form is really sent, its buttons are disabled for 8 seconds
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || form.method.toLowerCase() !== "post") return;
    setTimeout(function () {
      if (e.defaultPrevented) return;                         // a confirm() said no: leave the buttons alone
      if (form.dataset.sent === "1") return;
      form.dataset.sent = "1";
      form.querySelectorAll("button[type=submit], button:not([type]), input[type=submit]").forEach(function (b) { b.disabled = true; });
      setTimeout(function () {
        form.dataset.sent = "";
        form.querySelectorAll("button, input[type=submit]").forEach(function (b) { b.disabled = false; });
      }, 8000);
    }, 0);
  }, false);
  // browser "back" restores the page with disabled buttons: undo that
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) document.querySelectorAll("form[data-sent='1']").forEach(function (f) {
      f.dataset.sent = ""; f.querySelectorAll("button, input[type=submit]").forEach(function (b) { b.disabled = false; });
    });
  });
})();
