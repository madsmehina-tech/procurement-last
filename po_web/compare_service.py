# -*- coding: utf-8 -*-
"""
compare_service.py - quote comparison (مقارنة عروض الأسعار): reading, matching, analysis and the award.

The flow: offers are uploaded (read by quote2po - PDF / Word / Excel / images) -> the person checks every line -> the lines
of all offers are matched into UNIFIED items (suggested by the system, CONFIRMED by the person, remembered for next time) ->
the comparison table is computed on the SAME basis for every offer:

    * prices WITHOUT VAT (an offer marked «شاملة الضريبة» is divided by 1.15),
    * the REQUIRED quantity of each unified item (not the quantity each supplier happened to write),
    * missing items flagged, and an ADJUSTED total that completes them at the average price of the other offers,
    * every price judged against the price history of the system (purchase orders), when the item was bought before.

Nothing here is decided by the system: it flags and ranks, the person awards.
"""
import re
import unicodedata
from datetime import datetime

import db
import items_service
from textnorm import clean_spaces, norm_key

VAT = 0.15
GREEN_BELOW, RED_ABOVE, VERY_LOW = -5.0, 10.0, -30.0          # % against the historical average
MIN_COMPLETE = 80.0                                          # % of the items an offer must cover to be «best value»
AUTO_SCORE, SUGGEST_SCORE = 0.80, 0.45                       # similarity of two item names
EPS = 0.005
ST_DRAFT, ST_ACTIVE, ST_AWARDED, ST_CANCELLED = "مسودة", "قيد المقارنة", "تمت الترسية", "ملغاة"
KINDS = ("مواد", "مقاولين", "خدمات")


PLACEHOLDER = "عرض بلا اسم"                            # an offer whose supplier could not be read from the file: the person names it on the review page


class CompareError(Exception):
    """A rule was broken - the message is shown as is."""


# ---------------------------------------------------------------- reading names
_DIG = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIG.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_STOP = {"مقطع", "نوع", "ماركه", "صنف", "مم", "ملم", "mm", "بوصه", "انش", "inch", "ذو", "ذات", "من", "مع", "او", "و", "في", "ب", "ل", "كهربايي", "قياس", "مقاس", "حجم", "عدد", "الواح", "x", "ال"}
_UNITS = {"متر": ("م", "متر", "م.ط", "م.ط.", "متر طولي", "meter", "m", "mtr", "مترطولي"), "عدد": ("عدد", "حبه", "حبة", "قطعه", "قطعة", "pcs", "pc", "no", "nos", "ea", "each", "وحده", "وحدة", "عدد/قطعة"),
          "كيس": ("كيس", "شكاره", "شكارة", "bag"), "طن": ("طن", "ton", "tons"), "كجم": ("كجم", "كغ", "كيلو", "kg", "كيلوجرام"), "لتر": ("لتر", "l", "ltr", "liter", "litre"),
          "م2": ("م2", "م٢", "متر مربع", "m2", "sqm", "م²"), "م3": ("م3", "م٣", "متر مكعب", "m3", "cbm", "م³"), "لفة": ("لفة", "لفه", "رول", "roll"),
          "مقطوعية": ("مقطوعية", "مقطوعه", "مقطوعة", "ls", "lump sum", "lumpsum", "l.s"), "يوم": ("يوم", "day"), "ساعة": ("ساعة", "ساعه", "hr", "hour"), "شهر": ("شهر", "month")}
_UNIT_MAP = {a: k for k, vs in _UNITS.items() for a in vs}


def canonical_unit(u):
    key = clean_spaces(u or "").lower().replace("ـ", "")
    return _UNIT_MAP.get(key) or _UNIT_MAP.get(key.replace(" ", "")) or clean_spaces(u or "")


def norm_text(s):
    """Arabic-aware normal form: no marks / tatweel, one form of alef / yaa / taa, ASCII digits, one multiplication sign."""
    s = unicodedata.normalize("NFKC", s or "").translate(_DIG).lower()
    s = re.sub("[\u064b-\u065f\u0670\u0640]", "", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه").replace("ئ", "ي").replace("ؤ", "و")
    s = re.sub(r"[×*✕]", " x ", s)
    s = re.sub(r"(\d)\s*x\s*(\d)", r"\1 x \2", s)
    s = re.sub(r"(\d)(?=[^\d\s.x])", r"\1 ", s)
    s = re.sub(r"[^\w\s.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _specs(s):
    """The numbers of an item name (4×16 -> {4, 16}; 16 مم -> {16}): the sizes that make two names the SAME or a DIFFERENT item."""
    return {n.rstrip(".") for n in re.findall(r"\d+(?:\.\d+)?", norm_text(s))}


def _tokens(s):
    return {t for t in norm_text(s).split() if t not in _STOP and not re.fullmatch(r"[\d.]+", t) and len(t) > 1}


def _trigrams(s):
    t = " " + re.sub(r"[\d.]+", "", norm_text(s)).strip() + " "
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) > 2 else {t}


def similarity(a, b):
    """0..1 : how likely two written names are the SAME item (words, letters, and above all the SIZES)."""
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ga, gb = _trigrams(a), _trigrams(b)
    dice = 2 * len(ga & gb) / (len(ga) + len(gb)) if (ga and gb) else 0.0
    score = 0.55 * jac + 0.35 * dice
    sa, sb = _specs(a), _specs(b)
    if sa and sb:
        if not (sa & sb):
            score *= 0.35                                    # 4×16 against 6×25: another cable
        elif sa <= sb or sb <= sa:
            score = min(1.0, score + 0.10)                  # 16 against 4×16: compatible
        else:
            score *= 0.7
    elif sa or sb:
        score *= 0.85
    la, lb = norm_text(a).split(), norm_text(b).split()
    if la and lb and la[0] == lb[0] and len(la[0]) > 2 and score >= 0.3:
        score = min(1.0, score + 0.04)                       # the same head word (كابل ... كابل ...)
    if norm_text(a) == norm_text(b) and norm_text(a):
        score = 1.0
    return round(min(score, 1.0), 3)


# ---------------------------------------------------------------- creating
def create(title, kind, project, site, description, user):
    title = clean_spaces(title)
    if not title:
        raise CompareError("اكتب اسم المقارنة.")
    if kind not in KINDS:
        kind = KINDS[0]
    import masterdata
    with db.transaction() as tx:
        pid = sid = None
        project, site = clean_spaces(project), clean_spaces(site)
        if project:
            pid, project, _n = masterdata.resolve_project(project, tx)
            if site:
                sid, site, _n = masterdata.resolve_site(pid, site, tx)
        return tx.execute_insert("INSERT INTO [Comparisons] ([Title], [Kind], [ProjectID], [SiteID], [ProjectName], [SiteName], [Description], [Status], [Confirmed], [CreatedBy], [CreatedAt]) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)", (title[:150], kind, pid, sid, project or None, site or None, clean_spaces(description)[:255], ST_DRAFT, user, datetime.now()))


def get(compare_id):
    rows = db.query("SELECT * FROM [Comparisons] WHERE [CompareID] = ?", (compare_id,))
    return rows[0] if rows else None


def _editable(c):
    if not c:
        raise CompareError("المقارنة غير موجودة.")
    if c["Status"] in (ST_AWARDED, ST_CANCELLED):
        raise CompareError("المقارنة «%s» ولا تُعدَّل." % c["Status"])


def is_placeholder(name):
    return not name or str(name).startswith(PLACEHOLDER)


def _taken(compare_id, name, except_quote=None):
    return any(norm_key(q["PartyName"]) == norm_key(name) and q["QuoteID"] != except_quote and not is_placeholder(q["PartyName"])
               for q in db.query("SELECT * FROM [Quotes] WHERE [CompareID] = ?", (compare_id,)))


def add_quote(compare_id, party_kind, party_name, supplier_id, sub_id, quote_no, quote_date, vat, payment_terms, delivery_days, valid_until, declared_total, engine, original, lines, user=""):
    """A new offer with its lines (name, unit, qty, price). When the supplier's name could not be read it is saved as «عرض بلا اسم …»:
    the person names it on the review page (a comparison cannot be awarded while an offer has no name). Returns the quote id."""
    c = get(compare_id)
    _editable(c)
    party_name = clean_spaces(party_name)
    if not party_name:
        n = 1 + len([q for q in db.query("SELECT * FROM [Quotes] WHERE [CompareID] = ?", (compare_id,)) if is_placeholder(q["PartyName"])])
        party_name = "%s %d" % (PLACEHOLDER, n)
    elif _taken(compare_id, party_name):
        raise CompareError("لهذا الطرف عرض في هذه المقارنة بالفعل؛ عدّله أو احذفه أولًا.")
    with db.transaction() as tx:
        qid = tx.execute_insert("INSERT INTO [Quotes] ([CompareID], [PartyKind], [SupplierID], [SubcontractorID], [PartyName], [QuoteNo], [QuoteDate], [VatIncluded], [PaymentTerms], [DeliveryDays], "
                                "[ValidUntil], [DeclaredTotal], [Engine], [OriginalFile], [CreatedAt]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (compare_id, party_kind, supplier_id, sub_id, party_name[:150], (quote_no or "")[:60], quote_date, vat if vat in ("yes", "no") else "", (payment_terms or "")[:150],
                                 delivery_days, valid_until, declared_total, engine or "", (original or "")[:255], datetime.now()))
        _insert_lines(tx, qid, lines)
        tx.execute("UPDATE [Comparisons] SET [Status] = ?, [Confirmed] = 0 WHERE [CompareID] = ? AND [Status] = ?", (ST_ACTIVE, compare_id, ST_DRAFT))
    return qid


def _num(x):
    try:
        return float(str(x).replace(",", "")) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _insert_lines(tx, qid, lines):
    n = 0
    for ln in lines:
        name = clean_spaces(ln.get("name") or ln.get("RawName") or "")
        if not name:
            continue
        n += 1
        tx.execute("INSERT INTO [QuoteLines] ([QuoteID], [LineNo], [RawName], [Unit], [Qty], [UnitPrice], [Confidence], [Locked]) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                   (qid, n, name[:255], canonical_unit(ln.get("unit") or ln.get("Unit"))[:30], _num(ln.get("qty", ln.get("Qty"))), _num(ln.get("price", ln.get("UnitPrice")))))


def replace_lines(quote_id, lines):
    """The person edited the lines of an offer: they are replaced (assignments of unchanged names are kept)."""
    q = db.query("SELECT * FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
    if not q:
        raise CompareError("العرض غير موجود.")
    _editable(get(q[0]["CompareID"]))
    old = {norm_key(l["RawName"]): l for l in db.query("SELECT * FROM [QuoteLines] WHERE [QuoteID] = ?", (quote_id,))}
    with db.transaction() as tx:
        tx.execute("DELETE FROM [QuoteLines] WHERE [QuoteID] = ?", (quote_id,))
        _insert_lines(tx, quote_id, lines)
        for l in tx.query("SELECT * FROM [QuoteLines] WHERE [QuoteID] = ?", (quote_id,)):
            o = old.get(norm_key(l["RawName"]))
            if o and o.get("ItemID"):
                tx.execute("UPDATE [QuoteLines] SET [ItemID] = ?, [Confidence] = ?, [Locked] = ? WHERE [LineID] = ?", (o["ItemID"], o.get("Confidence") or 0, o.get("Locked") or 0, l["LineID"]))
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 0 WHERE [CompareID] = ?", (q[0]["CompareID"],))


def set_party(quote_id, kind, name, supplier_id=None, sub_id=None):
    """The person names the supplier / contractor of an offer (also corrects a name that was misread from the file)."""
    q = db.query("SELECT * FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
    if not q:
        raise CompareError("العرض غير موجود.")
    _editable(get(q[0]["CompareID"]))
    name = clean_spaces(name)
    if not name or is_placeholder(name):
        raise CompareError("اكتب اسم المورد أو المقاول صاحب العرض.")
    if _taken(q[0]["CompareID"], name, quote_id):
        raise CompareError("لطرف آخر بهذا الاسم عرض في هذه المقارنة بالفعل.")
    db.execute("UPDATE [Quotes] SET [PartyKind] = ?, [PartyName] = ?, [SupplierID] = ?, [SubcontractorID] = ? WHERE [QuoteID] = ?",
               (kind if kind in ("supplier", "contractor") else "supplier", name[:150], supplier_id, sub_id, quote_id))


def update_quote(quote_id, **fields):
    q = db.query("SELECT * FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
    if not q:
        raise CompareError("العرض غير موجود.")
    _editable(get(q[0]["CompareID"]))
    allowed = ("QuoteNo", "QuoteDate", "VatIncluded", "PaymentTerms", "DeliveryDays", "ValidUntil", "DeclaredTotal", "Notes")
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append("[%s] = ?" % k)
            params.append(v)
    if sets:
        db.execute("UPDATE [Quotes] SET %s WHERE [QuoteID] = ?" % ", ".join(sets), tuple(params) + (quote_id,))


def delete_quote(quote_id):
    q = db.query("SELECT * FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
    if not q:
        return
    _editable(get(q[0]["CompareID"]))
    with db.transaction() as tx:
        tx.execute("DELETE FROM [QuoteLines] WHERE [QuoteID] = ?", (quote_id,))
        tx.execute("DELETE FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 0 WHERE [CompareID] = ?", (q[0]["CompareID"],))


# ---------------------------------------------------------------- matching the lines into unified items
def _catalog():
    try:
        return items_service.build()
    except Exception:                                    # noqa - before the tables exist
        return []


def _aliases():
    try:
        return {a["AliasKey"]: a for a in db.query("SELECT * FROM [ItemAliases]")}
    except Exception:                                    # noqa
        return {}


def rematch(compare_id):
    """Matches every line that is not locked by hand: first what the system LEARNED, then the similarity of the names.
    Lines of different offers with the same item share a unified item; one offer never has two lines in the same item.
    Returns {"auto": n, "review": n, "new": n}."""
    c = get(compare_id)
    _editable(c)
    quotes = db.query("SELECT * FROM [Quotes] WHERE [CompareID] = ?", (compare_id,))
    qids = [q["QuoteID"] for q in quotes]
    lines = [l for q in qids for l in db.query("SELECT * FROM [QuoteLines] WHERE [QuoteID] = ?", (q,))]
    aliases, catalog = _aliases(), _catalog()
    items = {i["ItemID"]: i for i in db.query("SELECT * FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,))}
    stats = {"auto": 0, "review": 0, "new": 0}
    taken = {}                                           # item id -> quote ids already inside it
    for l in lines:
        if l.get("Locked") and l.get("ItemID") in items:
            taken.setdefault(l["ItemID"], set()).add(l["QuoteID"])
    members = {}                                         # item id -> names of its lines (for the similarity)
    for l in lines:
        if l.get("Locked") and l.get("ItemID") in items:
            members.setdefault(l["ItemID"], []).append(l["RawName"])
    order = sorted((l for l in lines if not (l.get("Locked") and l.get("ItemID") in items)), key=lambda l: (len([x for x in lines if x["QuoteID"] == l["QuoteID"]]) * -1, l["QuoteID"], l["LineNo"]))
    with db.transaction() as tx:
        sort_no = max([i["SortNo"] or 0 for i in items.values()] + [0])
        for l in order:
            target, score, source = None, 0.0, ""
            al = aliases.get(norm_key(l["RawName"]))
            if al:                                       # what was confirmed before
                for iid, it in items.items():
                    if norm_key(it["Name"]) == norm_key(al["Name"]) and l["QuoteID"] not in taken.get(iid, set()):
                        target, score, source = iid, 0.95, "alias"
                        break
            if not target:
                for iid, it in items.items():
                    if l["QuoteID"] in taken.get(iid, set()):
                        continue
                    sc = max([similarity(l["RawName"], it["Name"])] + [similarity(l["RawName"], n) for n in members.get(iid, [])])
                    if sc > score:
                        target, score, source = iid, sc, "sim"
            if target and (score >= SUGGEST_SCORE):
                stats["auto" if score >= AUTO_SCORE else "review"] += 1
            else:
                sort_no += 1
                name = (al or {}).get("Name") or l["RawName"]
                target = tx.execute_insert("INSERT INTO [ComparisonItems] ([CompareID], [SortNo], [Name], [Unit], [RequiredQty], [QtyFixed]) VALUES (?, ?, ?, ?, ?, 0)",
                                           (compare_id, sort_no, name[:255], (l.get("Unit") or "")[:30], l.get("Qty") or 0))
                items[target] = {"ItemID": target, "Name": name, "SortNo": sort_no}
                score = 1.0
                stats["new"] += 1
            tx.execute("UPDATE [QuoteLines] SET [ItemID] = ?, [Confidence] = ? WHERE [LineID] = ?", (target, score, l["LineID"]))
            taken.setdefault(target, set()).add(l["QuoteID"])
            members.setdefault(target, []).append(l["RawName"])
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 0 WHERE [CompareID] = ?", (compare_id,))
        _tidy_items(tx, compare_id)
    return stats


def _tidy_items(tx, compare_id):
    """Removes unified items that no line points to any more."""
    used = {l["ItemID"] for l in tx.query("SELECT [ItemID] FROM [QuoteLines] WHERE [QuoteID] IN (SELECT [QuoteID] FROM [Quotes] WHERE [CompareID] = ?)", (compare_id,))}
    for i in tx.query("SELECT [ItemID] FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,)):
        if i["ItemID"] not in used:
            tx.execute("DELETE FROM [ComparisonItems] WHERE [ItemID] = ?", (i["ItemID"],))


def assign(compare_id, assignments, user=""):
    """The person's matching: {line_id: item_id | 'new'}. A hand assignment is LOCKED (a later re-match never touches it)."""
    c = get(compare_id)
    _editable(c)
    qids = {q["QuoteID"] for q in db.query("SELECT [QuoteID] FROM [Quotes] WHERE [CompareID] = ?", (compare_id,))}
    with db.transaction() as tx:
        items = {i["ItemID"] for i in tx.query("SELECT [ItemID] FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,))}
        sort_no = max([i["SortNo"] or 0 for i in tx.query("SELECT [SortNo] FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,))] + [0])
        for line_id, target in assignments.items():
            ln = tx.query("SELECT * FROM [QuoteLines] WHERE [LineID] = ?", (int(line_id),))
            if not ln or ln[0]["QuoteID"] not in qids:
                continue
            ln = ln[0]
            if str(target) == "new":
                sort_no += 1
                target = tx.execute_insert("INSERT INTO [ComparisonItems] ([CompareID], [SortNo], [Name], [Unit], [RequiredQty], [QtyFixed]) VALUES (?, ?, ?, ?, ?, 0)",
                                           (compare_id, sort_no, ln["RawName"][:255], (ln.get("Unit") or "")[:30], ln.get("Qty") or 0))
                items.add(target)
            target = int(target)
            if target not in items:
                continue
            clash = [x for x in tx.query("SELECT * FROM [QuoteLines] WHERE [ItemID] = ? AND [QuoteID] = ?", (target, ln["QuoteID"])) if x["LineID"] != ln["LineID"]]
            if clash:
                raise CompareError("العرض نفسه له سطر آخر في هذا البند («%s»)؛ انقل أحد السطرين لبند آخر." % clash[0]["RawName"])
            tx.execute("UPDATE [QuoteLines] SET [ItemID] = ?, [Confidence] = 1, [Locked] = 1 WHERE [LineID] = ?", (target, ln["LineID"]))
        _tidy_items(tx, compare_id)
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 0 WHERE [CompareID] = ?", (compare_id,))


def update_items(compare_id, names=None, qtys=None, units=None):
    """The person's wording of each unified item, its unit and the REQUIRED quantity (typed = fixed, otherwise the most common quantity)."""
    _editable(get(compare_id))
    with db.transaction() as tx:
        for iid, name in (names or {}).items():
            name = clean_spaces(name)
            if name:
                tx.execute("UPDATE [ComparisonItems] SET [Name] = ? WHERE [ItemID] = ? AND [CompareID] = ?", (name[:255], int(iid), compare_id))
        for iid, unit in (units or {}).items():
            tx.execute("UPDATE [ComparisonItems] SET [Unit] = ? WHERE [ItemID] = ? AND [CompareID] = ?", (canonical_unit(unit)[:30], int(iid), compare_id))
        for iid, q in (qtys or {}).items():
            if str(q).strip() == "":
                tx.execute("UPDATE [ComparisonItems] SET [QtyFixed] = 0 WHERE [ItemID] = ? AND [CompareID] = ?", (int(iid), compare_id))
            else:
                tx.execute("UPDATE [ComparisonItems] SET [RequiredQty] = ?, [QtyFixed] = 1 WHERE [ItemID] = ? AND [CompareID] = ?", (_num(q), int(iid), compare_id))
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 0 WHERE [CompareID] = ?", (compare_id,))


def confirm_matching(compare_id, user=""):
    """«تأكيد المطابقة»: every match is confirmed; what was confirmed is REMEMBERED (ItemAliases) so the next comparison starts smarter."""
    c = get(compare_id)
    _editable(c)
    items = {i["ItemID"]: i for i in db.query("SELECT * FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,))}
    now = datetime.now()
    with db.transaction() as tx:
        for q in tx.query("SELECT * FROM [Quotes] WHERE [CompareID] = ?", (compare_id,)):
            for l in tx.query("SELECT * FROM [QuoteLines] WHERE [QuoteID] = ?", (q["QuoteID"],)):
                it = items.get(l.get("ItemID"))
                if not it:
                    continue
                tx.execute("UPDATE [QuoteLines] SET [Locked] = 1, [Confidence] = 1 WHERE [LineID] = ?", (l["LineID"],))
                key = norm_key(l["RawName"])
                if not key or key == norm_key(it["Name"]):
                    continue
                old = tx.query("SELECT * FROM [ItemAliases] WHERE [AliasKey] = ?", (key,))
                if old:
                    tx.execute("UPDATE [ItemAliases] SET [Name] = ?, [Unit] = ?, [UseCount] = ?, [LastUsedAt] = ? WHERE [AliasID] = ?", (it["Name"][:255], (it.get("Unit") or "")[:30], (old[0].get("UseCount") or 0) + 1, now, old[0]["AliasID"]))
                else:
                    tx.execute("INSERT INTO [ItemAliases] ([AliasKey], [Name], [Unit], [UseCount], [LastUsedAt]) VALUES (?, ?, ?, 1, ?)", (key[:255], it["Name"][:255], (it.get("Unit") or "")[:30], now))
        tx.execute("UPDATE [Comparisons] SET [Confirmed] = 1 WHERE [CompareID] = ?", (compare_id,))


# ---------------------------------------------------------------- the analysis
def _history(item_name, unit, catalog, prior):
    """The price history of one unified item: the purchase orders of the system (the main source) and the prices offered in earlier comparisons."""
    best, score = None, 0.0
    for it in catalog:
        sc = similarity(item_name, it["name"])
        if sc > score:
            best, score = it, sc
    out = {"po": None, "prior": None}
    if best and score >= 0.72:
        same_unit = not unit or not best.get("unit") or canonical_unit(unit) == canonical_unit(best["unit"])
        out["po"] = {"name": best["name"], "last": best["last_price"], "avg": best["avg_price"], "max": best["max_price"], "min": best["min_price"], "count": best["count"], "unit": best.get("unit") or "",
                     "unit_note": "" if same_unit else "وحدة الشراء السابق «%s» تختلف عن «%s»" % (best.get("unit") or "", unit)}
    ps = [p for p in prior if similarity(item_name, p["name"]) >= 0.80]
    if ps:
        vals = [p["price"] for p in ps]
        out["prior"] = {"avg": round(sum(vals) / len(vals), 2), "min": min(vals), "max": max(vals), "count": len(vals)}
    return out


def _prior_offers(compare_id):
    """Prices (without VAT) offered in OTHER comparisons - a second, clearly separate source of history."""
    out = []
    quotes = {q["QuoteID"]: q for q in db.query("SELECT * FROM [Quotes]") if q["CompareID"] != compare_id}
    for l in db.query("SELECT * FROM [QuoteLines]"):
        q = quotes.get(l["QuoteID"])
        if q and (l.get("UnitPrice") or 0) > 0:
            out.append({"name": l["RawName"], "price": _ex_vat(l["UnitPrice"], q.get("VatIncluded"))})
    return out


def _ex_vat(price, vat):
    return round(float(price or 0) / (1 + VAT), 4) if vat == "yes" else round(float(price or 0), 4)


def judge(price, avg):
    """(code, pct): ok-low = below the average, near, high = well above, very-low = suspiciously low (check the specification)."""
    if not avg or avg <= 0 or price <= 0:
        return "", None
    pct = round((price / avg - 1) * 100, 1)
    if pct < VERY_LOW:
        return "very-low", pct
    if pct < GREEN_BELOW:
        return "low", pct
    if pct <= RED_ABOVE:
        return "near", pct
    return "high", pct


def analyze(compare_id):
    """Everything the comparison table, the decision panel and the exports show."""
    c = get(compare_id)
    if not c:
        return None
    quotes = sorted(db.query("SELECT * FROM [Quotes] WHERE [CompareID] = ?", (compare_id,)), key=lambda q: q["QuoteID"])
    items = sorted(db.query("SELECT * FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,)), key=lambda i: (i["SortNo"] or 0, i["ItemID"]))
    lines = {}
    for q in quotes:
        for l in db.query("SELECT * FROM [QuoteLines] WHERE [QuoteID] = ?", (q["QuoteID"],)):
            lines[(l.get("ItemID"), q["QuoteID"])] = l
    catalog, prior = _catalog(), _prior_offers(compare_id)
    rows = []
    for it in items:
        offers = {q["QuoteID"]: lines[(it["ItemID"], q["QuoteID"])] for q in quotes if (it["ItemID"], q["QuoteID"]) in lines}
        qtys = [l["Qty"] for l in offers.values() if (l["Qty"] or 0) > 0]
        common = max(set(qtys), key=qtys.count) if qtys else 0.0
        required = it["RequiredQty"] if it.get("QtyFixed") else (common or it.get("RequiredQty") or 0)
        units = [canonical_unit(l.get("Unit")) for l in offers.values() if l.get("Unit")]
        unit = it.get("Unit") or (max(set(units), key=units.count) if units else "")
        hist = _history(it["Name"], unit, catalog, prior)
        avg = (hist["po"] or {}).get("avg") or ((hist["prior"] or {}).get("avg") if not hist["po"] else None)
        hist_source = "po" if hist["po"] else ("prior" if hist["prior"] else "")
        cells, prices = {}, []
        for q in quotes:
            l = offers.get(q["QuoteID"])
            if not l:
                cells[q["QuoteID"]] = {"present": False}
                continue
            ex = _ex_vat(l["UnitPrice"], q.get("VatIncluded"))
            code, pct = judge(ex, avg)
            cells[q["QuoteID"]] = {"present": True, "line": l, "raw_price": l["UnitPrice"] or 0, "price": ex, "qty": l["Qty"] or 0, "total": round(ex * required, 2), "quoted_total": round((l["Qty"] or 0) * (l["UnitPrice"] or 0), 2),
                                   "qty_diff": bool(required and l["Qty"] and abs(l["Qty"] - required) > EPS), "unit_diff": bool(unit and l.get("Unit") and canonical_unit(l["Unit"]) != canonical_unit(unit)),
                                   "code": code, "pct": pct, "review": (l.get("Confidence") or 0) < AUTO_SCORE and not l.get("Locked"), "lowest": False, "no_price": ex <= 0}
            if ex > 0:
                prices.append(ex)
        low = min(prices) if prices else None
        for cell in cells.values():
            cell["lowest"] = bool(cell.get("present") and low is not None and cell["price"] > 0 and abs(cell["price"] - low) < 1e-9 and len(prices) > 1)
        others_avg = round(sum(prices) / len(prices), 4) if prices else 0.0
        rows.append({"item": it, "name": it["Name"], "unit": unit, "required": required, "qty_fixed": bool(it.get("QtyFixed")), "cells": cells, "history": hist, "hist_avg": avg, "hist_source": hist_source,
                     "avg_offer": others_avg, "n_offers": len(prices), "spread": round((max(prices) / min(prices) - 1) * 100, 1) if len(prices) > 1 and min(prices) > 0 else 0.0})
    summary = []
    for q in quotes:
        present = [r for r in rows if r["cells"][q["QuoteID"]].get("present") and not r["cells"][q["QuoteID"]].get("no_price")]
        missing = [r for r in rows if not r["cells"][q["QuoteID"]].get("present")]
        normalized = round(sum(r["cells"][q["QuoteID"]]["total"] for r in present), 2)
        completing = 0.0
        for r in missing:                                 # the missing items completed at the AVERAGE price of the offers that DO have them
            completing += (r["avg_offer"] or 0) * (r["required"] or 0)
        quoted = round(sum(r["cells"][q["QuoteID"]]["quoted_total"] for r in present), 2)
        declared = q.get("DeclaredTotal")
        flags = []
        if is_placeholder(q["PartyName"]):
            flags.append("اسم المورد غير محدد: حدّده من صفحة مراجعة العرض")
        if missing:
            flags.append("ينقصه %d من %d بنود" % (len(missing), len(rows)))
        if q.get("VatIncluded") not in ("yes", "no"):
            flags.append("الضريبة غير محددة (عُدّت غير شاملة)")
        if declared and quoted and abs(declared - quoted) / max(declared, 1) > 0.01 and abs(declared - quoted * (1 + VAT)) / max(declared, 1) > 0.01:
            flags.append("إجمالي العرض المكتوب (%s) لا يطابق مجموع سطوره (%s): راجع القراءة" % ("{:,.2f}".format(declared), "{:,.2f}".format(quoted)))
        if q.get("ValidUntil") and q["ValidUntil"] < datetime.now():
            flags.append("العرض منتهي الصلاحية")
        no_price = [r for r in rows if r["cells"][q["QuoteID"]].get("present") and r["cells"][q["QuoteID"]].get("no_price")]
        if no_price:
            flags.append("%d بنود بلا سعر" % len(no_price))
        reds = sum(1 for r in rows if r["cells"][q["QuoteID"]].get("code") == "high")
        very_low = sum(1 for r in rows if r["cells"][q["QuoteID"]].get("code") == "very-low")
        if very_low:
            flags.append("%d أسعار منخفضة جدًا عن المعتاد (تأكد من المواصفات)" % very_low)
        summary.append({"quote": q, "name": q["PartyName"], "normalized": normalized, "adjusted": round(normalized + completing, 2), "quoted": quoted, "completing": round(completing, 2),
                        "present": len(rows) - len(missing), "missing": [r["name"] for r in missing], "complete": round((len(rows) - len(missing)) / len(rows) * 100, 1) if rows else 0.0,
                        "lowest_items": sum(1 for r in rows if r["cells"][q["QuoteID"]].get("lowest")), "red": reds, "very_low": very_low, "flags": flags})
    panel = _panel(summary)
    return {"compare": c, "quotes": quotes, "rows": rows, "summary": summary, "panel": panel, "review": sum(1 for r in rows for cell in r["cells"].values() if cell.get("review")),
            "confirmed": bool(c.get("Confirmed")), "n_items": len(rows)}


def _panel(summary):
    """The decision panel: best price, lowest total, most complete, best value. Ranking only - the person awards."""
    if not summary:
        return {}
    by = lambda key, rev=False: sorted(summary, key=key, reverse=rev)
    best_price = by(lambda s: (-s["lowest_items"], s["adjusted"]))[0]
    lowest = by(lambda s: s["normalized"])[0]
    most_complete = by(lambda s: (-s["complete"], s["adjusted"]))[0]
    eligible = [s for s in summary if s["complete"] >= MIN_COMPLETE] or summary
    best_value = sorted(eligible, key=lambda s: (s["adjusted"], s["red"]))[0]
    cheapest_adjusted = by(lambda s: s["adjusted"])[0]
    return {"best_price": best_price, "lowest_total": lowest, "most_complete": most_complete, "best_value": best_value, "cheapest_adjusted": cheapest_adjusted,
            "value_note": "أقل إجمالي معدّل بين العروض التي تغطي %d%% أو أكثر من البنود" % MIN_COMPLETE}


# ---------------------------------------------------------------- the award
def award(compare_id, quote_id, reason, user=""):
    """«اعتماد العرض الفائز»: a written reason is REQUIRED when the winner is not the cheapest (adjusted) offer. Saves the saving too."""
    a = analyze(compare_id)
    if not a:
        raise CompareError("المقارنة غير موجودة.")
    _editable(a["compare"])
    if not a["summary"]:
        raise CompareError("لا عروض في هذه المقارنة.")
    unnamed = [x["name"] for x in a["summary"] if is_placeholder(x["name"])]
    if unnamed:
        raise CompareError("حدّد اسم المورد لـ «%s» من صفحة مراجعة العرض قبل الترسية." % "، ".join(unnamed))
    if not a["confirmed"]:
        raise CompareError("أكّد مطابقة البنود أولًا (زر «تأكيد المطابقة» في خطوة المطابقة).")
    win = next((s for s in a["summary"] if s["quote"]["QuoteID"] == int(quote_id)), None)
    if not win:
        raise CompareError("العرض ليس من هذه المقارنة.")
    panel = a["panel"]
    reason = clean_spaces(reason)
    cheapest, best = panel["cheapest_adjusted"]["quote"]["QuoteID"], panel["best_value"]["quote"]["QuoteID"]
    qid = win["quote"]["QuoteID"]
    why = []
    if qid != cheapest and qid != best:
        why.append("ليس الأرخص ولا «أفضل قيمة»")
    if win["complete"] < MIN_COMPLETE:
        why.append("يغطي %d%% فقط من البنود" % win["complete"])
    if win["very_low"]:
        why.append("فيه أسعار منخفضة جدًا عن المعتاد")
    if why and len(reason) < 5:
        raise CompareError("اكتب سبب الترسية: العرض الفائز " + " و".join(why) + ".")
    others = [s["adjusted"] for s in a["summary"] if s is not win]
    vs_avg = round(sum(others) / len(others) - win["adjusted"], 2) if others else 0.0
    vs_max = round(max(s["adjusted"] for s in a["summary"]) - win["adjusted"], 2)
    q = win["quote"]
    next_kind = "عقد" if (q.get("PartyKind") == "contractor" or a["compare"].get("Kind") == "مقاولين") else "أمر شراء"
    db.execute("UPDATE [Comparisons] SET [Status] = ?, [WinnerQuoteID] = ?, [AwardReason] = ?, [WinnerTotal] = ?, [SavingsVsAvg] = ?, [SavingsVsMax] = ?, [AwardedAt] = ?, [AwardedBy] = ?, [NextKind] = ? WHERE [CompareID] = ?",
               (ST_AWARDED, q["QuoteID"], reason[:255], win["adjusted"], vs_avg, vs_max, datetime.now(), user, next_kind, compare_id))
    return {"winner": win, "savings_vs_avg": vs_avg, "savings_vs_max": vs_max, "next_kind": next_kind}


def reopen(compare_id):
    """Back to the comparison (the award is cancelled; nothing was created from it)."""
    c = get(compare_id)
    if not c or c["Status"] != ST_AWARDED:
        raise CompareError("لا ترسية لإلغائها.")
    if c.get("NextRef"):
        raise CompareError("أُنشئ من هذه الترسية «%s %s»؛ لا يمكن إلغاؤها." % (c.get("NextKind") or "", c["NextRef"]))
    db.execute("UPDATE [Comparisons] SET [Status] = ?, [WinnerQuoteID] = NULL, [AwardReason] = NULL, [WinnerTotal] = NULL, [SavingsVsAvg] = NULL, [SavingsVsMax] = NULL, [AwardedAt] = NULL, [AwardedBy] = NULL, [NextKind] = NULL WHERE [CompareID] = ?", (ST_ACTIVE, compare_id))


def link_next(compare_id, kind, ref):
    """The purchase order / contract that was created from the award."""
    if get(compare_id):
        db.execute("UPDATE [Comparisons] SET [NextKind] = ?, [NextRef] = ? WHERE [CompareID] = ?", (kind, str(ref)[:60], compare_id))


def delete_comparison(compare_id):
    c = get(compare_id)
    if not c:
        return
    if c["Status"] == ST_AWARDED:
        raise CompareError("لا تُحذف مقارنة تمت ترسيتها.")
    with db.transaction() as tx:
        for q in tx.query("SELECT [QuoteID] FROM [Quotes] WHERE [CompareID] = ?", (compare_id,)):
            tx.execute("DELETE FROM [QuoteLines] WHERE [QuoteID] = ?", (q["QuoteID"],))
        tx.execute("DELETE FROM [Quotes] WHERE [CompareID] = ?", (compare_id,))
        tx.execute("DELETE FROM [ComparisonItems] WHERE [CompareID] = ?", (compare_id,))
        tx.execute("DELETE FROM [Comparisons] WHERE [CompareID] = ?", (compare_id,))


# ---------------------------------------------------------------- what the next documents are filled with
def po_prefill(compare_id):
    """The purchase order form of the winning offer: supplier, project, site, the unified items with the REQUIRED quantity and the price without VAT."""
    a = analyze(compare_id)
    c = a["compare"]
    q = db.query("SELECT * FROM [Quotes] WHERE [QuoteID] = ?", (c.get("WinnerQuoteID"),))
    if not q:
        raise CompareError("لا عرض فائز لهذه المقارنة.")
    q = q[0]
    names, units, qtys, prices, missing = [], [], [], [], []
    for r in a["rows"]:
        cell = r["cells"][q["QuoteID"]]
        if not cell.get("present"):
            missing.append(r["name"])
            continue
        names.append(r["name"])
        units.append(r["unit"])
        qtys.append(("%g" % r["required"]) if r["required"] else "")
        prices.append(("%g" % cell["price"]) if cell["price"] else "")
    return {"form": {"supplier": q["PartyName"], "project": c.get("ProjectName") or "", "location": c.get("SiteName") or "", "quote_no": q.get("QuoteNo") or "", "item_name": names, "item_unit": units,
                     "item_qty": qtys, "item_price": prices, "compare_id": str(compare_id), "extra_notes": "بموجب مقارنة عروض الأسعار: %s" % c["Title"]},
            "missing": missing, "quote": q}


# ---------------------------------------------------------------- the statistics (kept from the first comparison)
def stats():
    rows = db.query("SELECT * FROM [Comparisons]")
    quotes = db.query("SELECT * FROM [Quotes]")
    awarded = [c for c in rows if c["Status"] == ST_AWARDED]
    parties = {}
    for q in quotes:
        p = parties.setdefault(norm_key(q["PartyName"]), {"name": q["PartyName"], "offers": 0, "wins": 0})
        p["offers"] += 1
    win_ids = {c["WinnerQuoteID"] for c in awarded}
    for q in quotes:
        if q["QuoteID"] in win_ids:
            parties[norm_key(q["PartyName"])]["wins"] += 1
    table = sorted(({**p, "rate": round(p["wins"] / p["offers"] * 100) if p["offers"] else 0} for p in parties.values()), key=lambda p: (-p["wins"], -p["offers"]))
    return {"count": len(rows), "awarded": len(awarded), "open": sum(1 for c in rows if c["Status"] in (ST_DRAFT, ST_ACTIVE)),
            "savings_avg": round(sum(c.get("SavingsVsAvg") or 0 for c in awarded), 2), "savings_max": round(sum(c.get("SavingsVsMax") or 0 for c in awarded), 2), "parties": table[:8]}


def get_quote_name(quote_id):
    q = db.query("SELECT [PartyName] FROM [Quotes] WHERE [QuoteID] = ?", (quote_id,))
    return q[0]["PartyName"] if q else ""
