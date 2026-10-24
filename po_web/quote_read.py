# -*- coding: utf-8 -*-
"""
quote_read.py - reads an offer file for the quote comparison, with extra care for SCANNED offers (OCR):

  * OCR is limited to the first pages (the price table is there; the other pages are drawings and terms that only add junk rows);
  * the scan is read with the configured languages AND with English alone - an English table under an Arabic letterhead is read far
    better without Arabic (which scrambles the word order); the better reading wins;
  * a row is accepted only when it PROVES itself: quantity x unit price = the total written on the line. Noise the OCR invents
    (table borders read as letters) is stripped from the description; rows that do not prove themselves and have no price are dropped;
  * the supplier's name loses the noise in front of it («ni ~ 4 i OMEGA …») and lone digits inside it.
"""
import re

import quote2po

_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])\d+(?:\.\d+)?(?![\w])")
UNIT_WORDS = {"m", "mtr", "no", "nos", "pcs", "pc", "set", "sets", "ls", "lot", "kg", "ton", "roll", "bag", "each", "ea", "عدد", "متر", "طقم", "لفة", "كيس", "حبة", "قطعة", "مقطوعية"}


def _f(tok):
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def clean_description(text):
    """Strips the noise OCR invents in front of / inside a description: leading item numbers and short lowercase Latin junk, stray symbols."""
    toks = text.split()
    while toks and (re.fullmatch(r"[\W_]+|\d{1,2}[.)]?", toks[0]) or re.fullmatch(r"[a-z]{1,4}", toks[0])):
        toks.pop(0)
    out = " ".join(toks)
    out = re.sub(r"\s+[|\\\[\]{}~_]+\s*$", "", out).strip(" |_~")
    return re.sub(r"\s{2,}", " ", out)


_TOTALS = re.compile(r"(?i)(sub\s*-?\s*total|grand\s*total|total\s*(sar|amount|price)?\s*[:\-]|\bvat\b|v\.a\.t)|(المجموع|الاجمالي|الإجمالي|إجمالي|اجمالي|الضريبة)")


def ocr_rows(md):
    """Rows that prove themselves: (description, unit, qty, price, total) with qty x price = total (1% or 1 riyal).
    The items end where the TOTALS begin (Sub Total / VAT / الإجمالي): what follows - terms, drawings, other pages - is not read as items."""
    rows = []
    for line in (md or "").splitlines():
        line = line.replace("|", " ").strip()
        toks = line.split()
        if rows and _TOTALS.search(line) and any(_NUM.fullmatch(t) for t in toks):
            break
        nums = [(i, _f(t)) for i, t in enumerate(toks) if _NUM.fullmatch(t) and _f(t) is not None]
        if len(nums) < 3:
            continue
        found = None
        for a in range(len(nums)):
            for b in range(a + 1, len(nums)):
                for c in range(b + 1, len(nums)):
                    q, p, t = nums[a][1], nums[b][1], nums[c][1]
                    if q and p and t and q > 0 and p > 0 and abs(q * p - t) <= max(1.0, 0.01 * t):
                        found = (nums[a][0], q, p, t)
        if not found:
            continue
        qi, q, p, t = found
        head = toks[:qi]
        unit = ""
        if head and head[-1].lower().strip(".") in UNIT_WORDS:
            unit = head.pop().strip(".")
        desc = clean_description(" ".join(head))
        if len(desc) >= 4 and re.search(r"[A-Za-z\u0600-\u06FF]{3,}", desc):
            rows.append({"name": desc, "unit": unit, "qty": q, "price": p, "total": t})
    return rows


def clean_party(name):
    toks = (name or "").split()
    while toks and (re.fullmatch(r"[\W_]+|\d+", toks[0]) or re.fullmatch(r"[a-z]{1,3}", toks[0])):
        toks.pop(0)
    toks = [t for i, t in enumerate(toks) if not (re.fullmatch(r"\d", t) and 0 < i < len(toks) - 1 and re.search(r"[A-Za-z\u0600-\u06FF]", toks[i - 1]) and re.search(r"[A-Za-z\u0600-\u06FF]", toks[i + 1]))]
    return " ".join(toks)


def _merge(proven, parsed):
    """Proven rows are trusted as they are: what only the normal parser found in a SCAN is mostly noise (borders, drawings), so it is not added.
    With no proven row at all the normal parser's rows are used, after dropping the ones without a price or a real name."""
    if proven:
        seen = {(r["qty"], r["price"]) for r in proven}
        extra = [it for it in parsed if (float(it.get("qty") or 0), float(it.get("price") or 0)) not in seen]
        return list(proven), len(extra)
    out, dropped, seen = [], 0, set()
    for it in parsed:
        key = (float(it.get("qty") or 0), float(it.get("price") or 0))
        name = clean_description(it.get("name") or "")
        if key not in seen and key[0] > 0 and key[1] > 0 and len(name) >= 6 and re.search(r"[A-Za-z\u0600-\u06FF]{3,}\s+[A-Za-z\u0600-\u06FF]{2,}", name):
            out.append({"name": name, "unit": it.get("unit") or "", "qty": key[0], "price": key[1], "total": key[0] * key[1]})
            seen.add(key)
        else:
            dropped += 1
    return out, dropped


def read_offer(path, known=(), exclude=(), pages=(1, 3)):
    """-> {md, engine, res, items, notes}. Never raises for a file it cannot read: the caller decides (types by hand / AI)."""
    notes = []
    quote2po._TLS.pages = pages
    quote2po._TLS.lang = None
    try:
        try:
            md, engine = quote2po.convert_to_md(path)
        except quote2po.UnsafeFile as e:                            # refused before unpacking: the person types the order by hand
            return {"md": "", "engine": "unsafe", "res": {"items": []}, "items": [], "notes": ["لم يُقرأ الملف: %s" % e]}
        res = quote2po.parse_quote(md, known=known, exclude=exclude)
        if engine != "ocr":
            return {"md": md, "engine": engine, "res": res, "items": res.get("items", []), "notes": notes}
        best = (md, res, ocr_rows(md))
        langs = quote2po.ocr_languages()
        if len(langs) > 1 and "eng" in langs:                    # the same scan read as English only: better when the table is English
            quote2po._TLS.lang = "eng"
            try:
                md2, _e = quote2po.convert_to_md(path)
                res2 = quote2po.parse_quote(md2, known=known, exclude=exclude)
                rows2 = ocr_rows(md2)
                if len(rows2) > len(best[2]):
                    best = (md2, res2, rows2)
                    notes.append("قُرئ الملف بالإنجليزية فقط لأنها أعطت أسطرًا أدق.")
            except Exception:                                    # noqa - the first reading stays
                pass
            finally:
                quote2po._TLS.lang = None
        md, res, rows = best
        items, dropped = _merge(rows, res.get("items", []))
        if rows:
            notes.append("%d بند تأكدنا منها حسابيًا (الكمية × السعر = الإجمالي المكتوب)." % len(rows))
        if dropped:
            notes.append("لم تُقبل %d أسطر أخرى من القراءة الضوئية (غير مؤكدة حسابيًا أو بلا سعر)؛ أضف أي بند ناقص يدويًا." % dropped)
        res = dict(res, items=items, supplier=clean_party(res.get("supplier") or ""))
        return {"md": md, "engine": engine, "res": res, "items": items, "notes": notes}
    finally:
        quote2po._TLS.pages = None
        quote2po._TLS.lang = None
