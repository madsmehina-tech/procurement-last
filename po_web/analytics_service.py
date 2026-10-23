# -*- coding: utf-8 -*-
"""
analytics_service.py - every number of the purchase-orders dashboard, computed ONCE and used by both the screen
(routes/analytics.py) and the Excel report (excel_dashboard.py), so the two can never disagree.
"""
from datetime import datetime

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
STATUS_ORDER = ["مكتمل", "بانتظار السداد", "بانتظار الفاتورة", "بانتظار الاستلام"]

COLUMNS = ("[PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [PaymentDate], "
           "[InvoiceStatus], [ReceiptStatus]")


def build(rows):

    def ym(dt):
        return (dt.year, dt.month)

    def shift(k, d):
        t = k[0] * 12 + (k[1] - 1) + d
        return (t // 12, t % 12 + 1)

    def growth(cur, prev):
        return None if not prev else round((cur - prev) / prev * 100, 1)

    total_count = len(rows)
    total_value = paid_value = 0.0
    m_count, m_value, m_paid, m_open, m_supp = {}, {}, {}, {}, {}
    supplier_paid, project_paid, project_status = {}, {}, {}

    for r in rows:
        amt = r["TotalAmount"] or 0
        supp = r["SupplierName"] or "-"
        proj = r["Project"] or "-"
        paid = r["PaymentStatus"] == "تم السداد"
        od = r["OrderDate"]
        total_value += amt
        if paid:
            paid_value += amt
            status = "مكتمل"
        elif r["InvoiceStatus"] == "تم الإصدار":
            status = "بانتظار السداد"
        elif r["ReceiptStatus"] in ("استلام جزئي", "مكتمل"):
            status = "بانتظار الفاتورة"
        else:
            status = "بانتظار الاستلام"
        st = project_status.setdefault(proj, {})
        st[status] = st.get(status, 0) + 1
        if od:
            k = ym(od)
            m_count[k] = m_count.get(k, 0) + 1
            m_value[k] = m_value.get(k, 0) + amt
            if not paid:
                m_open[k] = m_open.get(k, 0) + amt
        if paid:
            supplier_paid[supp] = supplier_paid.get(supp, 0) + amt
            project_paid[proj] = project_paid.get(proj, 0) + amt
            pdate = r.get("PaymentDate") or od      # actual payment month; order month for legacy rows
            if pdate:
                pk = ym(pdate)
                m_paid[pk] = m_paid.get(pk, 0) + amt
                m_supp[(supp, pk)] = m_supp.get((supp, pk), 0) + amt

    keys = set(m_count) | set(m_paid)
    end = max(keys) if keys else ym(datetime.now())
    window = [shift(end, i) for i in range(-5, 1)]
    months = [{"key": "%d-%02d" % k, "label": AR_MONTHS[k[1] - 1], "sub": str(k[0])} for k in window]

    def series(d):
        return [round(d.get(k, 0), 2) for k in window]

    s_count, s_value, s_paid, s_open = series(m_count), series(m_value), series(m_paid), series(m_open)

    kpis = [
        {"key": "count", "ar": "إجمالي أوامر الشراء التراكمي", "en": "Total Purchase Orders",
         "value": total_count, "growth": growth(s_count[-1], s_count[-2]), "spark": s_count, "good_up": True},
        {"key": "value", "ar": "إجمالي قيمة أوامر الشراء المعتمدة", "en": "Approved Purchase Orders Value",
         "value": round(total_value), "growth": growth(s_value[-1], s_value[-2]), "spark": s_value, "good_up": True},
        {"key": "paid", "ar": "إجمالي قيمة أوامر الشراء المصروفة", "en": "Actual Paid Purchase Orders",
         "value": round(paid_value), "growth": growth(s_paid[-1], s_paid[-2]), "spark": s_paid, "good_up": True},
        {"key": "open", "ar": "إجمالي قيمة أوامر الشراء غير المصروفة", "en": "Outstanding Purchase Orders Value",
         "value": round(total_value - paid_value), "growth": growth(s_open[-1], s_open[-2]),
         "spark": s_open, "good_up": False},
    ]

    max_c = max(s_count) or 1
    bars = [{"label": m["label"], "sub": m["sub"], "count": c, "pct": round(c / max_c * 100, 1),
             "latest": i == len(months) - 1} for i, (m, c) in enumerate(zip(months, s_count))]

    top = sorted(supplier_paid.items(), key=lambda x: -x[1])[:6]
    max_s = top[0][1] if top else 1
    suppliers = [{"name": n, "value": round(v), "share": round(v / paid_value * 100) if paid_value else 0,
                  "pct": round(v / max_s * 100, 1), "initial": (n.strip()[:1] or "?")} for n, v in top]

    pp = sorted(project_paid.items(), key=lambda x: -x[1])
    head, tail = pp[:5], pp[5:]
    if tail:
        head.append(("أخرى", sum(v for _, v in tail)))
    projects = [{"name": n, "value": round(v), "share": round(v / paid_value * 100) if paid_value else 0,
                 "color": i, "filterable": n != "أخرى"} for i, (n, v) in enumerate(head)]

    pstack = []
    for proj, st in project_status.items():
        pstack.append({"name": proj, "total": sum(st.values()),
                       "parts": [{"status": s_, "n": st.get(s_, 0)} for s_ in STATUS_ORDER]})
    pstack.sort(key=lambda x: -x["total"])
    pstack = pstack[:8]
    max_p = pstack[0]["total"] if pstack else 1
    for row in pstack:
        for part in row["parts"]:
            part["w"] = round(part["n"] / max_p * 100, 1)

    top4 = [n for n, _ in sorted(supplier_paid.items(), key=lambda x: -x[1])[:4]]
    trend = [{"name": n, "values": [round(m_supp.get((n, k), 0), 2) for k in window]} for n in top4]
    return dict(
        kpis=kpis, months=months, bars=bars, orders_growth=growth(s_count[-1], s_count[-2]),
        total_count=total_count, paid_value=round(paid_value), suppliers=suppliers, projects=projects,
        pstack=pstack, status_order=STATUS_ORDER, trend=trend,
    )
