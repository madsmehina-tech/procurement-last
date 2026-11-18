# -*- coding: utf-8 -*-
"""«إعدادات الطباعة» (29.0) - the settings of each printed document + its watermark + its QR code.

Each document (purchase order, voucher, payment order of a certificate, payment-cycle sheet, the two statements) keeps its own settings
(CompanyProfile.PrintJson, one dict per document). Every value has a «كما هو» default that leaves the paper exactly as version 28 printed it,
except the two the management chose to switch on for every document: the watermark (مسودة / ملغى, from the document's REAL status)
and the QR code.

The QR code carries the main data of the paper as plain text (readable by any phone, no internet, no access to the system) and a
six-character verification code: HMAC(server secret, document|number|amount). Somebody who changes the amount on the paper cannot make
a matching code; «التحقق من مستند» recomputes it from the database.
"""
import hashlib
import hmac
import io
import json
import logging
from datetime import date, datetime

import db

log = logging.getLogger(__name__)

DOCS = {
    "po": {"name": "أمر شراء", "states": True},
    "voucher": {"name": "أمر صرف عام", "states": True},
    "cycle": {"name": "كشف دورة الصرف", "states": True},
    "statement": {"name": "كشف حساب مقاول", "states": False},
    "supplier_statement": {"name": "كشف حساب مورد", "states": False},
}

# key: (label, {value: label}) - the first value of every option is «as the document already prints»
OPTIONS = {
    "paper": ("الورق", {"": "كما هو", "A4": "A4", "A5": "A5"}),
    "orient": ("الاتجاه", {"": "كما هو", "portrait": "عمودي", "landscape": "أفقي"}),
    "margin": ("الهوامش", {"": "كما هي", "6mm": "ضيقة", "10mm": "عادية", "16mm": "واسعة"}),
    "logo_pos": ("مكان الشعار", {"": "كما هو", "right": "يمين", "left": "يسار"}),
    "logo_size": ("حجم الشعار", {"": "كما هو", "0.75": "أصغر", "1.3": "أكبر"}),
    "font": ("خط الطباعة", {"": "كما هو", "cairo": "Cairo", "tajawal": "Tajawal", "plex": "IBM Plex Arabic"}),
    "size": ("حجم الخط", {"": "عادي", "0.92": "أصغر", "1.08": "أكبر"}),
}
FLAGS = {
    "show_cr": "السجل التجاري في الترويسة",
    "show_vat": "الرقم الضريبي في الترويسة",
    "show_addr": "العنوان والهاتف في الترويسة",
    "watermark": "العلامة المائية (مسودة / ملغى)",
    "qr": "رمز QR للتحقق",
}
DEFAULT = {"paper": "", "orient": "", "margin": "", "logo_pos": "", "logo_size": "", "font": "", "size": "", "footer": "",
           "show_cr": False, "show_vat": False, "show_addr": False, "watermark": True, "qr": True}
FONT_FAMILY = {"cairo": '"Cairo"', "tajawal": '"Tajawal"', "plex": '"IBM Plex Sans Arabic"'}


def clean(raw):
    raw = raw if isinstance(raw, dict) else {}
    s = dict(DEFAULT)
    for key, (_label, values) in OPTIONS.items():
        v = str(raw.get(key) or "")
        if v in values:
            s[key] = v
    for key in FLAGS:
        if key in raw:
            s[key] = bool(raw[key])
    s["footer"] = " ".join(str(raw.get("footer") or "").split())[:200]
    return s


def from_form(form):
    raw = {k: form.get(k, "") for k in OPTIONS}
    raw.update({k: form.get(k) == "1" for k in FLAGS})
    raw["footer"] = form.get("footer", "")
    return raw


_cache = {"all": None}


def _all():
    if _cache["all"] is not None:
        return _cache["all"]
    data = {}
    try:
        rows = db.query("SELECT [PrintJson] FROM [CompanyProfile] WHERE [ProfileID] = 1")
        if rows and rows[0].get("PrintJson"):
            data = json.loads(rows[0]["PrintJson"]) or {}
    except Exception:                                           # noqa - before m035, or a broken value: the defaults
        log.debug("print settings not readable", exc_info=True)
    _cache["all"] = data if isinstance(data, dict) else {}
    return _cache["all"]


def get(doc):
    return clean(_all().get(doc))


def save(doc, raw):
    data = dict(_all())
    data[doc] = clean(raw)
    text = json.dumps(data, ensure_ascii=False)
    if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
        db.execute("UPDATE [CompanyProfile] SET [PrintJson] = ? WHERE [ProfileID] = 1", (text,))
    else:
        db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [PrintJson]) VALUES (1, ?)", (text,))
    _cache["all"] = data
    return data[doc]


def reset_cache():
    _cache["all"] = None


def describe(s):
    parts = [OPTIONS[k][1][s[k]] for k in ("paper", "orient", "margin", "font") if s[k]]
    parts += [label for k, label in FLAGS.items() if s[k] and k in ("watermark", "qr")]
    return "، ".join(parts) or "كما هو"


# ------------------------------------------------------------------ verification code + QR
def _secret():
    import config
    try:
        return config.secret_key().encode("utf-8") if isinstance(config.secret_key(), str) else config.secret_key()
    except Exception:                                           # noqa
        return b"po-web"


def _money(v):
    try:
        return "%.2f" % float(v or 0)
    except (TypeError, ValueError):
        return "0.00"


def code(doc, number, amount):
    msg = "%s|%s|%s" % (doc, str(number or "").strip().upper(), _money(amount))
    return hmac.new(_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:6].upper()


def _fmt_date(d):
    if isinstance(d, (datetime, date)):
        return d.strftime("%Y/%m/%d")
    return str(d or "")


DOC_EN = {"po": "Purchase Order", "voucher": "Payment Request", "cycle": "Payment Cycle", "statement": "Contractor Statement",
          "supplier_statement": "Supplier Statement"}


def qr_text(doc, number, amount, when=None, party="", state="", company=""):
    """What the QR code carries: SHORT and in Latin letters only, so that the code stays coarse enough to be read from paper by any phone
    (the Arabic names made it 61x61 tiny squares that phones could not read once printed). The verification code is what matters."""
    lines = ["AL-HADAB", "%s: %s" % (DOC_EN.get(doc, doc), number)]
    if when:
        lines.append("Date: %s" % _fmt_date(when))
    lines.append("Amount: {:,.2f} SAR".format(float(amount or 0)))
    lines.append("Code: %s" % code(doc, number, amount))
    return "\n".join(lines)


def qr_svg(text):
    """An inline <svg> (scales with its box), or '' when the QR library is missing (the code is still printed as text)."""
    try:
        import segno
    except ImportError:
        return ""
    try:
        q = segno.make(text, error="m", micro=False)
        out = io.BytesIO()
        # border=4: the white margin of 4 squares every reader needs around the code; light=white so it stays white on any paper setting
        q.save(out, kind="svg", scale=1, border=4, dark="#000", light="#fff", xmldecl=False, svgns=False, omitsize=True, nl=False)
        return out.getvalue().decode("utf-8").replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)
    except Exception:                                           # noqa - a QR problem must never stop a paper from printing
        log.exception("QR failed")
        return ""


def watermark_for(state):
    """The REAL status of the document -> the watermark (approved / paid / done: none)."""
    s = str(state or "")
    if s in ("ملغي", "ملغى", "ملغاة"):
        return s                                                # the document's own spelling (أمر الصرف «ملغي»، الدورة «ملغاة»)
    if s in ("مسودة", "معاينة"):                             # «بانتظار قرار الإدارة» is a working paper for the decision, not a draft
        return "مسودة"
    return ""


def kit(doc, number="", amount=0, when=None, party="", state=None, company=None, preview=False):
    """Everything a printed template needs (see _print_kit.html). `state` None = the document has no status (a statement)."""
    s = get(doc)
    comp = company if company is not None else _company()
    wm = ""
    if s["watermark"]:
        wm = "مسودة" if preview else watermark_for(state)
    text = svg = vcode = ""
    if s["qr"] and number:
        text = qr_text(doc, number, amount, when, party, state or "", comp.get("LegalName") or "")
        svg, vcode = qr_svg(text), code(doc, number, amount)
    info = []
    if s["show_cr"] and comp.get("CR"):
        info.append("السجل التجاري: %s" % comp["CR"])
    if s["show_vat"] and comp.get("VatNo"):
        info.append("الرقم الضريبي: %s" % comp["VatNo"])
    if s["show_addr"]:
        addr = " — ".join(x for x in (comp.get("Address"), comp.get("City"), comp.get("Phone")) if x)
        if addr:
            info.append(addr)
    zoom = float(s["size"] or 1)
    if s["paper"] == "A5" and s["orient"] != "landscape":
        zoom *= 0.72                                            # the forms are drawn for A4: on A5 everything is scaled down together
    return {"s": s, "doc": doc, "watermark": wm, "cancel": wm in ("ملغي", "ملغى", "ملغاة"), "qr_svg": svg, "qr_text": text, "code": vcode, "number": number,
            "info": info, "footer": s["footer"], "font": FONT_FAMILY.get(s["font"], ""), "font_face": font_face(s["font"]),
            "page_zoom": round(zoom, 3)}


_FONT_FILES = {"cairo": ("Cairo", "cairo", ("400", "700")), "tajawal": ("Tajawal", "tajawal", ("400", "700")), "plex": ("IBM Plex Sans Arabic", "plexar", ("400", "700"))}
_face_cache = {}


def font_face(font):
    """@font-face rules with the font INSIDE (data: URI): a PDF made from the page, or the page opened from a file, has the font too."""
    if font not in _FONT_FILES:
        return ""
    if font in _face_cache:
        return _face_cache[font]
    import base64
    import os
    family, slug, weights = _FONT_FILES[font]
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
    rules = []
    for w in weights:
        for sub in ("arabic", "latin"):
            path = os.path.join(here, "%s-%s-%s.woff2" % (slug, sub, w))
            if os.path.isfile(path):
                data = base64.b64encode(open(path, "rb").read()).decode("ascii")
                rules.append("@font-face { font-family: '%s'; font-weight: %s; src: url(data:font/woff2;base64,%s) format('woff2'); }"
                             % (family, "600 900" if w == "700" else "100 500", data))
    _face_cache[font] = "\n".join(rules)
    return _face_cache[font]


def statement_kit(doc, party_id, name, balance):
    """A statement: its number is the party + the day it was printed (ST-C12-20261116 / ST-S7-20261116), its amount the balance."""
    letter = "C" if doc == "statement" else "S"
    number = "ST-%s%s-%s" % (letter, party_id, datetime.now().strftime("%Y%m%d"))
    try:
        return kit(doc, number, balance, datetime.now(), name, state=None)
    except Exception:                                           # noqa
        log.exception("print kit failed")
        return None


def _company():
    try:
        rows = db.query("SELECT * FROM [CompanyProfile] WHERE [ProfileID] = 1")
        return rows[0] if rows else {}
    except Exception:                                           # noqa
        return {}


# ------------------------------------------------------------------ «التحقق من مستند»
def _lookup(number):
    """[(doc, number, amount, date, party, state)] of the documents with this number."""
    n = str(number or "").strip()
    out = []
    try:
        import po_files
        order, items = po_files.load_order(n)
        if order:
            out.append(("po", order["PONumber"], po_files.grand_of(order, items), order["OrderDate"], order["SupplierName"], ""))
    except Exception:                                           # noqa
        log.debug("po lookup", exc_info=True)
    try:
        for r in db.query("SELECT [VoucherNo], [VoucherDate], [PartyName], [Net], [Status] FROM [PaymentVouchers] WHERE [VoucherNo] = ?", (n,)):
            out.append(("voucher", r["VoucherNo"], r["Net"], r["VoucherDate"], r["PartyName"], r["Status"]))
    except Exception:                                           # noqa
        log.debug("voucher lookup", exc_info=True)
    try:
        for r in db.query("SELECT [CycleNo], [CycleDate], [Status] FROM [PaymentCycles] WHERE [CycleNo] = ?", (n,)):
            out.append(("cycle", r["CycleNo"], 0, r.get("CycleDate"), "", r["Status"]))
    except Exception:                                           # noqa
        log.debug("cycle lookup", exc_info=True)
    return out


def verify(number, given_code, amount=None):
    """{ok, found, doc_name, number, amount, date, party, state} - the code is recomputed from the database (or, for a statement, from the
    amount the person reads on the paper)."""
    given = str(given_code or "").strip().upper()
    hits = _lookup(number)
    for doc, no, amt, when, party, state in hits:
        if code(doc, no, amt) == given:
            return {"ok": True, "found": True, "doc_name": DOCS[doc]["name"], "number": no, "amount": float(amt or 0), "date": _fmt_date(when),
                    "party": party, "state": state}
    if amount not in (None, ""):
        for doc in ("statement", "supplier_statement"):
            if code(doc, number, amount) == given:
                return {"ok": True, "found": True, "doc_name": DOCS[doc]["name"], "number": number, "amount": float(amount), "date": "", "party": "", "state": ""}
    if hits:
        doc, no, amt, when, party, state = hits[0]
        return {"ok": False, "found": True, "doc_name": DOCS[doc]["name"], "number": no, "amount": float(amt or 0), "date": _fmt_date(when),
                "party": party, "state": state}
    return {"ok": False, "found": False}
