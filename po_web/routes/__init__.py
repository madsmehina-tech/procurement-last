# -*- coding: utf-8 -*-
"""
Route registry. Modules decorate their views with  @route(...)  and register_all() attaches them to the
Flask app with endpoint = function name - so every url_for('orders_list') etc. in the templates keeps
working, while the code lives in small modules instead of one 1400-line file.

register_all() also applies the permission guard to EVERY page from permissions.ENDPOINT_PERMS, and refuses
to start if a page has no entry there - a new page can never be left unprotected by mistake.
"""
import auth
import permissions

_REGISTRY = []


def route(rule, **options):
    def deco(func):
        _REGISTRY.append((rule, func, options))
        return func
    return deco


def register_all(app):
    from routes import (  # noqa: F401  (importing registers the routes)
        auth_routes, orders, analytics, suppliers, subcontractors, contracts, certificates, documents, system,
        settings, items, payments, cycles, archive_tab, ledger, reports, compare, contract_gen,
    )
    missing = [f.__name__ for _, f, _ in _REGISTRY if f.__name__ not in permissions.ENDPOINT_PERMS]
    if missing:
        raise RuntimeError("These pages have no permission in permissions.ENDPOINT_PERMS: %s" % ", ".join(missing))
    for rule, func, options in _REGISTRY:
        guarded = auth.guard(permissions.ENDPOINT_PERMS[func.__name__])(func)
        app.add_url_rule(rule, endpoint=func.__name__, view_func=guarded, **options)
