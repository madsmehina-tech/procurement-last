# -*- coding: utf-8 -*-
r"""
python tools\uat_report.py [YYYY-MM-DD]  - summarises logs\access.log / error.log for the field test:
requests per user, response times (median / 95th percentile), the slowest pages, errors.
Without a date it covers everything in the log.
"""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ (\w+) (\S+) (\d{3}) (\d+)ms user=(\S+) ip=(\S+)")


def pct(values, p):
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))]


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    path = os.path.join(config.LOG_DIR, "access.log")
    if not os.path.isfile(path):
        print("لا يوجد سجل بعد:", path)
        return 1
    n = 0
    times, by_path, by_user, by_ip, by_status = [], defaultdict(list), Counter(), Counter(), Counter()
    first = last = None
    for raw in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(raw)
        if not m or (day and m.group(1) != day):
            continue
        d, t, method, p, status, ms, user, ip = m.groups()
        ms = int(ms)
        n += 1
        times.append(ms)
        key = re.sub(r"/\d+", "/#", re.sub(r"/PO-[\w-]+", "/PO", p))
        by_path[method + " " + key].append(ms)
        by_user[user] += 1
        by_ip[ip] += 1
        by_status[status[0] + "xx"] += 1
        first = first or (d + " " + t)
        last = d + " " + t
    print("تقرير التجربة" + (" - " + day if day else ""))
    if not n:
        print("لا توجد طلبات في هذه الفترة.")
        return 0
    print("الفترة: %s → %s | الطلبات: %d" % (first, last, n))
    print("أجهزة مختلفة: %d | مستخدمون: %d" % (len(by_ip), len([u for u in by_user if u != '-'])))
    print("زمن الاستجابة: الوسيط %d ms | 95%% من الطلبات أسرع من %d ms | الأبطأ %d ms" % (pct(times, 50), pct(times, 95), max(times)))
    print("الحالات:", ", ".join("%s=%d" % kv for kv in sorted(by_status.items())))
    slow = sum(1 for t in times if t > 1500)
    print("طلبات أبطأ من 1.5 ثانية: %d (%.1f%%)" % (slow, 100.0 * slow / n))
    print("\nأبطأ الصفحات (بالمتوسط):")
    for k, v in sorted(by_path.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:8]:
        print("  %-42s %5d ms (×%d)" % (k[:42], sum(v) / len(v), len(v)))
    errs = os.path.join(config.LOG_DIR, "error.log")
    count = 0
    if os.path.isfile(errs):
        for raw in open(errs, encoding="utf-8", errors="replace"):
            if re.match(r"^\d{4}-\d{2}-\d{2} ", raw) and (not day or raw.startswith(day)):
                count += 1
                print("  خطأ:", raw.strip()[:150])
    print("\nأخطاء الخادم المسجّلة: %d" % count)
    ok = pct(times, 95) < 2000 and not by_status.get("5xx") and count == 0
    print("النتيجة: " + ("✔ ضمن الحدود المقبولة" if ok else "⚠ تحتاج مراجعة"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
