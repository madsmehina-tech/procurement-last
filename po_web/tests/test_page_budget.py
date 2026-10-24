# -*- coding: utf-8 -*-
"""A speed guard: no page may ask the database more than a fixed number of questions (every extra question is an extra delay on a slow disk)."""
import pytest

import conftest
import fake_db
import seed

BUDGET = {"/": 32,   # the home page now also carries the contractors' figures (same numbers as their dashboard)
           "/subcontractors": 35, "/certificates": 25, "/payments": 35, "/orders": 12, "/orders/new": 20, "/items": 20, "/suppliers": 10,
          "/cycles": 25, "/archive": 25, "/contracts": 15, "/subcontractors/dashboard": 30, "/orders/analytics": 12}


@pytest.fixture(scope="module")
def busy():
    conftest.build_schema()
    subs = seed.seed()
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c, subs


@pytest.mark.parametrize("page,limit", sorted(BUDGET.items()))
def test_page_stays_within_its_query_budget(busy, page, limit):
    c, _subs = busy
    fake_db.CALLS[0] = 0
    r = c.get(page)
    assert r.status_code == 200, (page, r.status_code)
    assert fake_db.CALLS[0] <= limit, "%s asked the database %d times (budget %d)" % (page, fake_db.CALLS[0], limit)


def test_the_contractor_page_does_not_ask_per_certificate(busy):
    c, subs = busy
    fake_db.CALLS[0] = 0
    assert c.get("/subcontractors/%d" % subs[0]).status_code == 200
    assert fake_db.CALLS[0] <= 40                                    # 27 certificates on this page; it used to be one question for each
