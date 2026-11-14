/* The charts + filters panel (28.0). One <section data-insights="page"> per page; data from /insights/<page>/data.json.
   The filter rules are the same as insights.apply() on the server, which makes the Excel export match the screen. */
(function () {
  "use strict";
  var COLORS = { main: "#29595E", gold: "#C08D34", navy: "#1F3A5C", ok: "#15803D", bad: "#B91C1C", teal: "#3E7C82", grey: "#94A3B8" };
  var GOOD = ["تم السداد", "مصروف", "معتمد", "تم التحويل", "مكتملة", "مغلقة", "تمت الترسية", "مرسّاة"], BAD = ["معلق", "ملغي", "مرفوض"];
  function norm(s) { return String(s || "").toLowerCase().replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").replace(/[ً-ْ]/g, "").replace(/\s+/g, " ").trim(); }
  function fmt(n, d) { return Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d }); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function uniq(a) { var seen = {}, out = []; a.forEach(function (x) { if (x && !seen[x]) { seen[x] = 1; out.push(x); } }); return out.sort(function (x, y) { return x.localeCompare(y, "ar"); }); }
  function group(rows, key) { var o = {}; rows.forEach(function (r) { var k = r[key] || "—"; o[k] = (o[k] || 0) + r.amount; }); return o; }
  function top(obj, n) { return Object.keys(obj).sort(function (a, b) { return obj[b] - obj[a]; }).slice(0, n); }
  function statusColor(s) { return GOOD.indexOf(s) >= 0 ? COLORS.ok : BAD.indexOf(s) >= 0 ? COLORS.bad : COLORS.gold; }

  function filterRows(rows, f) {
    var words = norm(f.q).split(" ").filter(Boolean);
    return rows.filter(function (r) {
      var m = (r.date || "").slice(0, 7);
      if (f.kind && r.kind !== f.kind) return false;
      if (f.from && (!m || m < f.from)) return false;
      if (f.to && (!m || m > f.to)) return false;
      if (f.project && r.project !== f.project) return false;
      if (f.party && r.party !== f.party) return false;
      if (f.status && r.status !== f.status) return false;
      if (words.length) { var hay = norm([r.no, r.party, r.project, r.status].join(" ")); if (!words.every(function (w) { return hay.indexOf(w) >= 0; })) return false; }
      return true;
    });
  }
  window.POInsights = { filterRows: filterRows };          // (used by the tests of the page)

  function start(box) {
    var page = box.dataset.insights, link = box.dataset.link === "1", charts = {}, data = { rows: [], meta: {} };
    var q = function (sel) { return box.querySelector(sel); }, fields = {};
    [].forEach.call(box.querySelectorAll("[data-ins-f]"), function (el) { fields[el.dataset.insF] = el; });
    function values() { var f = {}; Object.keys(fields).forEach(function (k) { f[k] = (fields[k].value || "").trim(); }); return f; }
    function options(sel, list, all) { var keep = sel.value; sel.innerHTML = '<option value="">' + all + "</option>" + list.map(function (x) { return '<option value="' + esc(x) + '">' + esc(x) + "</option>"; }).join(""); sel.value = list.indexOf(keep) >= 0 ? keep : ""; }

    function draw() {
      var f = values(), rows = filterRows(data.rows, f), total = rows.reduce(function (s, r) { return s + r.amount; }, 0);
      var done = rows.filter(function (r) { return GOOD.indexOf(r.status) >= 0; }).reduce(function (s, r) { return s + r.amount; }, 0);
      q("[data-ins-kpis]").innerHTML = [[fmt(rows.length, 0), "العدد"], [fmt(total), "إجمالي المبالغ (ريال)"], [fmt(rows.length ? total / rows.length : 0), "المتوسط"],
        [fmt(done), "المنجز (مسدَّد / مصروف / معتمد)"], [fmt(total - done), "الباقي"], [fmt(uniq(rows.map(function (r) { return r.party; })).length, 0), data.meta.party_label || "الطرف"]]
        .map(function (k) { return '<div class="kpi-card"><p class="kpi-label">' + esc(k[1]) + '</p><p class="kpi-value" style="font-size:18px;">' + k[0] + "</p></div>"; }).join("");
      var params = Object.keys(f).filter(function (k) { return f[k]; }).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(f[k]); }).join("&");
      q("[data-ins-xlsx]").href = box.dataset.export + (params ? "?" + params : "");
      var active = Object.keys(f).some(function (k) { return f[k]; });
      q("[data-ins-note]").textContent = (active ? "بعد الفلاتر: " : "") + rows.length + " من " + data.rows.length + " سجل. المبالغ بالريال (الأوامر بعملة أخرى بمعادلها حسب سعر الصرف المحفوظ معها).";
      if (link) {                                              // the page's own table follows the filters
        var keep = {}; rows.forEach(function (r) { keep[r.no] = 1; });
        [].forEach.call(document.querySelectorAll("[data-ins-key]"), function (tr) { tr.hidden = active && !keep[tr.dataset.insKey]; });
      } else {
        q("[data-ins-rows]").innerHTML = rows.slice(0, 15).map(function (r) {
          var no = r.href ? '<a href="' + esc(r.href) + '"><b>' + esc(r.no) + "</b></a>" : "<b>" + esc(r.no) + "</b>";
          return "<tr><td>" + esc((data.meta.kind || {})[r.kind] || r.kind) + "</td><td>" + no + "</td><td>" + esc(r.date) + "</td><td>" + esc(r.project) + "</td><td>" + esc(r.party) + "</td><td><b>" + fmt(r.amount) + "</b></td><td>" + esc(r.status) + "</td></tr>";
        }).join("") || '<tr><td colspan="7" style="text-align:center; padding:22px; color:var(--text-secondary);">لا سجلات بهذه الفلاتر.</td></tr>';
        if (rows.length > 15) q("[data-ins-note]").textContent += " يظهر أحدث 15؛ الكل في ملف Excel.";
      }
      if (typeof Chart === "undefined" || q("[data-ins-body]").hidden) return;
      Chart.defaults.font.family = "'Cairo', 'Segoe UI', Tahoma, sans-serif"; Chart.defaults.color = "#0F172A";
      var money = { callback: function (v) { return Number(v).toLocaleString("en-US"); } };
      function chart(name, cfg) { var cv = q('[data-ins-c="' + name + '"]'); if (!cv) return; if (charts[name]) charts[name].destroy(); charts[name] = new Chart(cv, cfg); }
      function hbar(name, obj, color) { var ks = top(obj, 8); chart(name, { type: "bar", data: { labels: ks, datasets: [{ label: "المبلغ (ريال)", data: ks.map(function (k) { return obj[k]; }), backgroundColor: color, borderRadius: 6 }] },
        options: { indexAxis: "y", maintainAspectRatio: false, scales: { x: { reverse: true, ticks: money }, y: { position: "right" } }, plugins: { legend: { display: false }, tooltip: { rtl: true } } } }); }
      var months = uniq(rows.map(function (r) { return (r.date || "").slice(0, 7); }));
      chart("month", { data: { labels: months, datasets: [
        { type: "bar", label: "المبلغ (ريال)", data: months.map(function (m) { return rows.filter(function (r) { return (r.date || "").slice(0, 7) === m; }).reduce(function (s, r) { return s + r.amount; }, 0); }), backgroundColor: COLORS.main, borderRadius: 6, yAxisID: "y" },
        { type: "line", label: "العدد", data: months.map(function (m) { return rows.filter(function (r) { return (r.date || "").slice(0, 7) === m; }).length; }), borderColor: COLORS.gold, backgroundColor: COLORS.gold, tension: 0.3, yAxisID: "y2" }] },
        options: { maintainAspectRatio: false, scales: { x: { reverse: true }, y: { position: "right", ticks: money }, y2: { position: "left", grid: { drawOnChartArea: false }, ticks: { precision: 0 } } }, plugins: { legend: { rtl: true }, tooltip: { rtl: true } } } });
      var st = group(rows, "status"), sk = top(st, 8);
      chart("status", { type: "doughnut", data: { labels: sk, datasets: [{ data: sk.map(function (k) { return st[k]; }), backgroundColor: sk.map(statusColor), borderColor: "#FFFFFF", borderWidth: 2 }] },
        options: { maintainAspectRatio: false, cutout: "60%", plugins: { legend: { rtl: true, position: "bottom" }, tooltip: { rtl: true, callbacks: { label: function (c) { return c.label + ": " + fmt(c.parsed) + " ريال"; } } } } } });
      hbar("party", group(rows, "party"), COLORS.navy);
      var hasProj = rows.some(function (r) { return r.project; }); q("[data-ins-projbox]").hidden = !hasProj; if (hasProj) hbar("project", group(rows, "project"), COLORS.teal);
      var kinds = uniq(data.rows.map(function (r) { return r.kind; })); q("[data-ins-kindchart]").hidden = kinds.length < 2;
      if (kinds.length > 1) { var gk = group(rows, "kind"); chart("kind", { type: "bar", data: { labels: kinds.map(function (k) { return (data.meta.kinds || {})[k] || k; }), datasets: [{ label: "المبلغ (ريال)", data: kinds.map(function (k) { return gk[k] || 0; }), backgroundColor: [COLORS.main, COLORS.gold, COLORS.navy, COLORS.teal], borderRadius: 6 }] },
        options: { maintainAspectRatio: false, scales: { x: { reverse: true }, y: { position: "right", ticks: money } }, plugins: { legend: { display: false }, tooltip: { rtl: true } } } }); }
    }

    function setup() {
      q("[data-ins-title]").textContent = data.meta.title || "الرسوم والفلاتر";
      [].forEach.call(box.querySelectorAll("[data-ins-partylab],[data-ins-partyhead]"), function (el) { el.textContent = data.meta.party_label || "الطرف"; });
      q("[data-ins-partytitle]").textContent = "أعلى " + (data.meta.party_label || "الأطراف") + " مبلغاً";
      var kinds = uniq(data.rows.map(function (r) { return r.kind; }));
      q("[data-ins-kindbox]").hidden = kinds.length < 2;
      fields.kind.innerHTML = '<option value="">الكل</option>' + kinds.map(function (k) { return '<option value="' + k + '">' + esc((data.meta.kinds || {})[k] || k) + "</option>"; }).join("");
      options(fields.project, uniq(data.rows.map(function (r) { return r.project; })), "كل المشاريع");
      options(fields.party, uniq(data.rows.map(function (r) { return r.party; })), "الكل");
      options(fields.status, uniq(data.rows.map(function (r) { return r.status; })), "كل الحالات");
      draw();
    }
    Object.keys(fields).forEach(function (k) { fields[k].addEventListener(fields[k].tagName === "SELECT" ? "change" : "input", draw); });
    q("[data-ins-reset]").addEventListener("click", function () { Object.keys(fields).forEach(function (k) { fields[k].value = ""; }); draw(); });
    q("[data-ins-print]").addEventListener("click", function () { document.body.classList.add("ins-printing"); window.print(); setTimeout(function () { document.body.classList.remove("ins-printing"); }, 500); });
    q("[data-ins-toggle]").addEventListener("click", function () { var b = q("[data-ins-body]"); b.hidden = !b.hidden; this.textContent = b.hidden ? "إظهار الرسوم" : "إخفاء الرسوم"; this.setAttribute("aria-expanded", b.hidden ? "false" : "true"); try { localStorage.setItem("ins-open-" + page, b.hidden ? "0" : "1"); } catch (e) {} if (!b.hidden) draw(); });
    try { var saved = localStorage.getItem("ins-open-" + page); if (saved === "0") { q("[data-ins-body]").hidden = true; q("[data-ins-toggle]").textContent = "إظهار الرسوم"; } } catch (e) {}
    fetch(box.dataset.url, { credentials: "same-origin" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { data = d; setup(); })
      .catch(function () { q("[data-ins-note]").textContent = "تعذّر تحميل بيانات الرسوم. حدّث الصفحة."; });
  }
  document.querySelectorAll("[data-insights]").forEach(start);
})();
