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

  // v27.3 - rows that open a page: the address lives in data-href (plain text, never inside JavaScript), a click or Enter follows it.
  // Only same-site addresses ("/...") are followed, so a value typed by a user can never become a script or an outside link.
  function safeGo(h) { if (h && h.charAt(0) === "/" && h.charAt(1) !== "/" && h.charAt(1) !== "\\") { location.href = h; } }
  document.querySelectorAll("tr[data-href]").forEach(function (tr) {
    tr.style.cursor = "pointer";
    tr.setAttribute("tabindex", "0");
    tr.setAttribute("role", "link");
    tr.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target === tr) { safeGo(tr.getAttribute("data-href")); }
    });
  });
  document.addEventListener("click", function (e) {
    var tr = e.target.closest ? e.target.closest("tr[data-href]") : null;
    if (!tr || e.target.closest("a, button, input, select, textarea, label, form")) return;
    safeGo(tr.getAttribute("data-href"));
  });

  // v27.3 - every label gets tied to its field (screen readers, and a tap on the label focuses the field)
  var autoId = 0;
  document.querySelectorAll("label:not([for])").forEach(function (lb) {
    if (lb.querySelector("input, select, textarea")) return;                    // already wrapping its field
    var f = lb.nextElementSibling;
    while (f && !/^(INPUT|SELECT|TEXTAREA)$/.test(f.tagName)) {
      var inner = f.querySelector && f.querySelector("input:not([type=hidden]), select, textarea");
      if (inner) { f = inner; break; }
      f = null;
    }
    if (!f) return;
    if (!f.id) { f.id = "fld-auto-" + (++autoId); }
    lb.setAttribute("for", f.id);
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


  // any [data-toggle="<selector>"] button shows / hides that element (the filter bar on a phone)
  document.querySelectorAll("[data-toggle]").forEach(function (b) {
    b.addEventListener("click", function () { var t = document.querySelector(b.dataset.toggle); if (t) t.classList.toggle("show"); });
  });

  // the bottom bar's «المزيد» opens the same menu as the top button
  document.querySelectorAll("[data-open-menu]").forEach(function (b) {
    b.addEventListener("click", function () { if (toggle) toggle.click(); window.scrollTo(0, 0); });
  });

  // on a phone every plain list table is shown as cards: each cell gets the name of its column (read from the table's own header)
  document.querySelectorAll("table.list").forEach(function (tb) {
    if (tb.classList.contains("no-cards")) return;
    var heads = [].slice.call(tb.querySelectorAll("thead th")).map(function (h) { return (h.textContent || "").trim(); });
    var rows = [].slice.call(tb.querySelectorAll("tbody tr"));
    if (!heads.length || !rows.length) return;
    var ok = rows.every(function (r) { var c = r.children; return c.length === heads.length && ![].some.call(c, function (x) { return x.colSpan > 1; }); });
    if (!ok) return;
    rows.forEach(function (r) { [].forEach.call(r.children, function (td, i) { td.setAttribute("data-label", heads[i]); }); });
    tb.classList.add("cards");
    var host = tb.closest(".card"); if (host) host.classList.add("cards-host");
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
