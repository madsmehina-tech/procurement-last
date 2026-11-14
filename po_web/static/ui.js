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

/* ---- v28.0: type-to-search on a long drop-down, and the signature-box editor (title + name rows) ---- */
(function () {
  function norm(s) { return String(s || "").toLowerCase().replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").replace(/[ً-ْ]/g, ""); }
  // <select data-search="placeholder">: a box above it; typing hides the options that do not contain the words (the select stays the real field)
  document.querySelectorAll("select[data-search]").forEach(function (sel) {
    var box = document.createElement("input");
    box.type = "search"; box.className = sel.className; box.placeholder = sel.dataset.search || "اكتب للبحث…"; box.autocomplete = "off";
    box.setAttribute("aria-label", box.placeholder); box.style.marginBottom = "4px";
    var note = document.createElement("small"); note.className = "set-hint"; note.style.margin = "0";
    sel.parentNode.insertBefore(box, sel); sel.parentNode.appendChild(note);
    function apply() {
      var words = norm(box.value).split(/\s+/).filter(Boolean), shown = [];
      [].forEach.call(sel.options, function (o) {
        if (!o.value) { o.hidden = false; return; }
        var t = norm(o.textContent), hit = words.every(function (w) { return t.indexOf(w) >= 0; });
        o.hidden = !hit; o.disabled = !hit && !o.selected ? true : false; if (hit) shown.push(o);
      });
      [].forEach.call(sel.querySelectorAll("optgroup"), function (g) { g.hidden = ![].some.call(g.children, function (o) { return !o.hidden; }); });
      if (!words.length) { note.textContent = ""; return; }
      note.textContent = shown.length ? (shown.length + " نتيجة" + (shown.length === 1 ? " — اختيرت تلقائياً" : " — اختر من القائمة")) : "لا نتائج لما كتبته";
      if (shown.length === 1 && sel.value !== shown[0].value) { sel.value = shown[0].value; sel.dispatchEvent(new Event("change", { bubbles: true })); }
      if (shown.length > 1) sel.size = 1;
    }
    box.addEventListener("input", apply);
    box.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); sel.focus(); } if (e.key === "ArrowDown") { e.preventDefault(); sel.focus(); } });
    new MutationObserver(function () { if (box.value) apply(); }).observe(sel, { childList: true });
  });

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-sign-add],[data-sign-del],[data-sign-up],[data-sign-down]"); if (!b) return;
    var ed = b.closest("[data-sign-editor]"), rows = ed.querySelector("[data-sign-rows]"), row = b.closest(".sign-row"), kind = ed.dataset.signEditor;
    if (b.hasAttribute("data-sign-add")) {
      var d = document.createElement("div"); d.className = "sign-row"; d.style.cssText = "display:grid; grid-template-columns: 1fr 1fr auto; gap:6px; align-items:center;";
      d.innerHTML = '<input class="inp" name="' + kind + '_sign_title" maxlength="60" placeholder="المسمّى (مثال: المدير المالي)" aria-label="المسمّى">' +
        '<input class="inp" name="' + kind + '_sign_name" maxlength="120" placeholder="الاسم (اختياري)" aria-label="الاسم">' +
        '<span style="display:flex; gap:4px;"><button type="button" class="btn" data-sign-up aria-label="تحريك للأعلى">▲</button><button type="button" class="btn" data-sign-down aria-label="تحريك للأسفل">▼</button><button type="button" class="btn" data-sign-del style="color:var(--danger);">حذف</button></span>';
      rows.insertBefore(d, rows.firstChild); d.querySelector("input").focus();          // a new box goes ABOVE the existing ones
    } else if (b.hasAttribute("data-sign-del")) { if (rows.children.length > 1) row.remove(); }
    else if (b.hasAttribute("data-sign-up")) { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); }
    else if (b.hasAttribute("data-sign-down")) { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); }
  });
})();
