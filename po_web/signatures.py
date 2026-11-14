# -*- coding: utf-8 -*-
"""
signatures.py - the signature boxes printed at the bottom of the purchase order and of the payment voucher.

Each is a list of {title, name} kept in «بيانات الشركة» (CompanyProfile.PoSignsJson / VoucherSignsJson). The list order is the printed order:
from the right, and from the TOP row when there are more boxes than fit on one line. A signature added later is put at the START of the list, so
it prints above the ones that were already there; rows() fills the LAST row first, which keeps the original boxes together on the bottom line.
"""
import json

import db
from textnorm import clean_spaces

PO, VOUCHER = "po", "voucher"
COLUMN = {PO: "PoSignsJson", VOUCHER: "VoucherSignsJson"}
PER_ROW = {PO: 4, VOUCHER: 5}
MAX_SIGNS = 12
DEFAULT_PO = ["مهندس العقود والمشتريات", "مدير إدارة العقود والمشتريات", "مدير عام المشاريع", "المدير العام"]
# the voucher boxes of the versions before 28.0: (title, the CompanyProfile column that held the name)
OLD_VOUCHER = (("مدير العقود والمشتريات", "SignContracts"), ("مدير عام المشاريع", "SignProjects"), ("مدير الحسابات", "SignAccounts"), ("المدير المالي", "SignFinance"), ("المدير العام", "SignGM"))


class SignError(Exception):
    pass


def _clean(items):
    out = []
    for it in items or []:
        title, name = clean_spaces((it or {}).get("title"))[:60], clean_spaces((it or {}).get("name"))[:120]
        if title:
            out.append({"title": title, "name": name})
    return out[:MAX_SIGNS]


def defaults(kind, company=None):
    c = company or {}
    if kind == PO:
        return [{"title": t, "name": ""} for t in DEFAULT_PO]
    return [{"title": t, "name": clean_spaces(c.get(col))} for t, col in OLD_VOUCHER]


def get(kind, company=None):
    """The configured list (the defaults until someone saves one)."""
    c = company if company is not None else (db.query("SELECT * FROM [CompanyProfile] WHERE [ProfileID] = 1") or [{}])[0]
    try:
        saved = _clean(json.loads(c.get(COLUMN[kind]) or "[]"))
    except (ValueError, TypeError):
        saved = []
    return saved or defaults(kind, c)


def parse(form, kind):
    """The lists typed in the settings page: <kind>_sign_title[] / <kind>_sign_name[]. Raises SignError with what to fix."""
    titles, names = form.getlist("%s_sign_title" % kind), form.getlist("%s_sign_name" % kind)
    raw = [{"title": t, "name": names[i] if i < len(names) else ""} for i, t in enumerate(titles)]
    if any(len(clean_spaces(r["title"])) > 60 for r in raw):
        raise SignError("مسمّى التوقيع أطول من 60 حرفاً.")
    if any(clean_spaces(r["name"]) and not clean_spaces(r["title"]) for r in raw):
        raise SignError("اكتب المسمّى لكل اسم في التوقيعات.")
    items = _clean(raw)
    if not items:
        raise SignError("أبقِ توقيعاً واحداً على الأقل.")
    if len([r for r in raw if clean_spaces(r["title"])]) > MAX_SIGNS:
        raise SignError("عدد التوقيعات أكثر من %d." % MAX_SIGNS)
    return items


def save(kind, items):
    db.execute("UPDATE [CompanyProfile] SET [%s] = ? WHERE [ProfileID] = 1" % COLUMN[kind], (json.dumps(_clean(items), ensure_ascii=False),))


def rows(items, per_row):
    """The boxes split into printed rows: the LAST row is full, a shorter first row holds what does not fit (the ones added later)."""
    items = list(items or [])
    if not items:
        return []
    first = len(items) % per_row
    out = [items[:first]] if first else []
    for i in range(first, len(items), per_row):
        out.append(items[i:i + per_row])
    return out


def from_voucher(sign_json, company=None):
    """The boxes saved WITH a voucher: a list (28.0+) or the old {key: name} dictionary of the five fixed boxes."""
    try:
        data = json.loads(sign_json or "null")
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list):
        return _clean(data) or get(VOUCHER, company)
    if isinstance(data, dict):
        keys = ("contracts", "projects", "accounts", "finance", "gm")
        return [{"title": t, "name": clean_spaces(data.get(k))} for k, (t, _col) in zip(keys, OLD_VOUCHER)]
    return get(VOUCHER, company)
