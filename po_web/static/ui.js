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

  // tabs: <nav class="tabs" data-key="x" data-start="name"> with [data-tab] buttons and .tab-pane[data-pane] sections.
  // The address (#tab-name) and the last choice are remembered; any [data-tab-go] link switches tab.
  document.querySelectorAll(".tabs[data-key]").forEach(function (nav) {
    var btns = [].slice.call(nav.querySelectorAll("[data-tab]")), names = btns.map(function (b) { return b.dataset.tab; });
    var panes = [].slice.call(document.querySelectorAll(".tab-pane")), key = "tab-" + nav.dataset.key;
    function show(name) {
      if (names.indexOf(name) < 0) name = names[0];
      panes.forEach(function (p) { p.hidden = p.dataset.pane !== name; });
      btns.forEach(function (b) { var on = b.dataset.tab === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false"); });
      try { sessionStorage.setItem(key, name); } catch (e) {}
      if (history.replaceState) history.replaceState(null, "", "#tab-" + name);
    }
    var start = (location.hash || "").slice(1).replace(/^tab-/, "");
    if (names.indexOf(start) < 0) { try { start = sessionStorage.getItem(key) || ""; } catch (e) { start = ""; } }
    if (nav.dataset.start) start = nav.dataset.start;
    show(start);
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-tab],[data-tab-go]");
      if (!b || (b.dataset.tab && !nav.contains(b) && !b.dataset.tabGo)) return;
      var name = b.dataset.tab || b.dataset.tabGo;
      if (names.indexOf(name) < 0) return;
      e.preventDefault(); show(name);
    });
  });
})();
