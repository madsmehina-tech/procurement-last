# -*- coding: utf-8 -*-
"""Run by hand:  python tests/measure_pages.py   - prints how many database calls (= Access connections) each page makes."""
import sys, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import conftest, fake_db, seed

conftest.build_schema()
subs = seed.seed()
import app as appmod
c = appmod.app.test_client()
with c.session_transaction() as s:
    s["user_id"] = 1
pages = ["/", "/login", "/subcontractors", "/subcontractors/%d" % subs[0], "/certificates", "/payments", "/orders", "/orders/new", "/orders/PO-2026-001", "/items",
         "/suppliers", "/cycles", "/archive", "/contracts", "/subcontractors/dashboard", "/orders/analytics", "/avatar/1"]
print("%-34s %6s %8s %6s" % ("page", "calls", "ms(fake)", "code"))
for p in pages:
    fake_db.CALLS[0] = 0
    t = time.perf_counter()
    r = c.get(p)
    print("%-34s %6d %8.0f %6s" % (p, fake_db.CALLS[0], (time.perf_counter() - t) * 1000, r.status_code))
