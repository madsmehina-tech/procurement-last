#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quote2po - reads a supplier quotation (PDF / DOCX / XLSX / HTML / MD / image),
converts it to Markdown and extracts the data needed for a purchase order.

    quote2po.py INPUT OUTPUT.txt [--ai] [--ai-mode auto|always|never] [--md OUTPUT.md]

Extraction strategy (three layers, cheapest first):
    1. Local rule-based parsing of the Markdown conversion (free, offline, fast).
    2. Automatic escalation to Claude (Anthropic API) whenever the local parse
       is missing, empty or low-confidence - not only for scanned images.
    3. If neither succeeds (or AI is disabled / unreachable), the Markdown
       table is dumped into a plain .xlsx file next to OUTPUT.txt so the data
       can still be copied by hand into the purchase order.

--ai-mode controls how eagerly step 2 is used:
    never   never call the AI (default, and the default when neither --ai nor
            --ai-mode is given, for full backward compatibility)
    auto    call the AI only when the local parse is empty / unreliable
    always  always call the AI first (still falls back to the local parse or
            to the .xlsx dump if the AI call itself fails)
--ai is kept as a legacy alias for "--ai-mode always".

OUTPUT.txt (UTF-8, TAB separated, read by the Excel workbook):
    status      ok|error
    error       <message>
    method      local|ai            (omitted when nothing could be extracted)
    confidence  high|medium|low     (omitted when nothing could be extracted)
    xlsx        <path>              (present only when the .xlsx fallback was written)
    supplier / quote_no / quote_date / note   <value>
    item        <name> <unit> <qty> <price>   (numbers use "." as decimal mark)

The Markdown conversion is always written next to OUTPUT.txt (or --md) so the
result can be checked.  AI extraction needs ANTHROPIC_API_KEY (environment
variable or config.ini).
"""
import sys, os, re, json, argparse, unicodedata, base64, configparser, threading
import urllib.request, urllib.error

VERSION = '2026.09.26'
HERE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------- text helpers
_DIG = {}
for i, ch in enumerate('٠١٢٣٤٥٦٧٨٩'):
    _DIG[ord(ch)] = str(i)
for i, ch in enumerate('۰۱۲۳۴۵۶۷۸۹'):
    _DIG[ord(ch)] = str(i)
_DIG[ord('٫')] = '.'
_DIG[0x06CC] = 'ي'   # Farsi yeh
_DIG[0x06A9] = 'ك'   # keheh
_DIG[0x06BE] = 'ه'   # heh doachashmee
_DIG[0x06C1] = 'ه'
_DIG[ord('٬')] = ','
_INVIS = re.compile('[​-‏‪-‮⁦-⁩﻿�­]')


def norm(s):
    """Normalise extracted text: NFKC (presentation forms -> letters), digits -> ASCII, drop bidi marks."""
    s = unicodedata.normalize('NFKC', s or '')
    s = s.translate(_DIG)
    s = _INVIS.sub('', s)
    s = s.replace('ـ', '')  # tatweel
    return re.sub(r'[ \t ]+', ' ', s).strip()


def ar_key(s):
    """Loose Arabic matching key (alef/hamza/ya/ta-marbuta folded, diacritics removed)."""
    s = norm(s).lower()
    s = re.sub('[ً-ٰٟ]', '', s)
    s = re.sub('[أإآٱ]', 'ا', s)
    s = s.replace('ى', 'ي').replace('ة', 'ه')
    return s


def num(tok):
    """'3,700.50' -> 3700.5 ; None when not numeric."""
    t = norm(tok).strip('()')
    t = re.sub(r'(?i)(sar|sr|ريال|ر\.?س|﷼)', '', t).strip()
    if not re.fullmatch(r'[-+]?\d[\d,]*(?:\.\d+)?%?', t):
        return None
    t = t.rstrip('%').replace(',', '')
    try:
        return float(t)
    except ValueError:
        return None


def fmt_num(v):
    if v is None:
        return ''
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return ('%.4f' % v).rstrip('0').rstrip('.')


# ----------------------------------------------------------------------------- converters
class NeedsAI(Exception):
    pass


def _split_scripts(word):
    """Split glued Arabic/Latin-digit runs:  'لفة20' -> 'لفة 20'."""
    w = re.sub(r'(?<=[؀-ۿ])(?=[0-9])', ' ', word)
    w = re.sub(r'(?<=[0-9])(?=[؀-ۿ])', ' ', w)
    return w


def _script(t):
    a = len(re.findall(r'[\u0600-\u06FF]', t))
    l = len(re.findall(r'[A-Za-z]', t))
    return 'ar' if a > l else ('lat' if l else '')


def order_words(ws, rtl):
    """Reading order of the words of one line/cell: by x in the base direction,
    with runs of the other script put back in their own direction (an English
    name inside an Arabic line, "شركة الصريف" inside an English one)."""
    ws = sorted(ws, key=lambda w: -w[0] if rtl else w[0])
    other = 'lat' if rtl else 'ar'
    out, run = [], []
    for w in ws:
        if _script(w[4]) == other:
            run.append(w)
        else:
            out.extend(reversed(run))
            run = []
            out.append(w)
    out.extend(reversed(run))
    return out


def _is_rtl(ws):
    txt = ' '.join(w[4] for w in ws)
    return len(re.findall(r'[\u0600-\u06FF]', txt)) > len(re.findall(r'[A-Za-z]', txt))


_CANON = {'desc': 'الوصف', 'qty': 'الكمية', 'price': 'سعر الوحدة', 'total': 'الإجمالي',
          'unit': 'الوحدة', 'serial': 'م', 'code': 'الرمز', 'other': '-'}
_TYPE_PRIORITY = ('desc', 'qty', 'price', 'total', 'unit', 'code', 'serial')


def _cluster_type(text):
    seq = header_sequence(text)
    for t in _TYPE_PRIORITY:
        if t in seq:
            return t
    return 'other'


def _extract_table_data(rows, order, bounds, mh, start_at=0):
    """Shared by a fresh header-detected table and a continuation page that
    reuses a previous page's column structure: classifies every row of
    `rows[start_at:]` into the given columns and pulls out the amount-bearing
    (item) rows, joining wrapped description lines to the nearest item row.
    Returns the markdown table lines (header + items), or None if no item
    row was found at all."""
    def yc(r):
        return sum((w[1] + w[3]) / 2.0 for w in r) / len(r)

    def line_text(r):
        return ' '.join(w[4] for w in r)

    by_x = sorted(order, key=lambda c: c['xc'])

    def col_of(w):
        x = (w[0] + w[2]) / 2.0
        k = 0
        while k < len(bounds) and x > bounds[k]:
            k += 1
        return by_x[k]

    numeric_ids = {id(c) for c in order if c['type'] in ('qty', 'price', 'total')}
    desc_ids = {id(c) for c in order if c['type'] == 'desc'}

    def is_amount(t):
        if '%' in t or not re.search(r'\d', t):
            return False
        v = num(t)
        # a repeated document header on a continuation page (phone numbers,
        # "26/08/2026"-style dates) must never be mistaken for a quantity or
        # price - no real line item is in the millions of units or riyals
        return v is not None and abs(v) < 1_000_000

    def attach(target, cells):
        for cid, ws in cells.items():
            if cid not in numeric_ids:
                target.setdefault(cid, []).extend(ws)

    data, pending = [], []
    for r in rows[start_at:]:
        y = yc(r)
        cells = {}
        for w in r:
            cells.setdefault(id(col_of(w)), []).append(w)
        is_data = any(is_amount(w[4]) for cid, ws in cells.items() if cid in numeric_ids for w in ws)
        label = ' '.join(w[4] for cid, ws in cells.items() for w in ws
                         if cid in desc_ids or (cid in numeric_ids and not is_amount(w[4])))
        if label and (TOTAL_ROW.search(ar_key(label)) or TOTAL_ROW.search(label)):
            if data:
                break
            continue
        if is_data:
            row = {'y': y, 'cells': cells}
            for py, pc in pending:
                if abs(py - y) <= 2.2 * mh:
                    attach(row['cells'], pc)
            pending = []
            data.append(row)
            continue
        if data and y - data[-1]['y'] > 2.6 * mh:
            break
        if data and abs(y - data[-1]['y']) <= 2.2 * mh:
            attach(data[-1]['cells'], cells)
        elif not data:
            pending.append((y, cells))
    if not data:
        return None

    def cell_text(ws):
        rtl_cell = _is_rtl(ws)
        lines = {}
        for w in ws:
            lines.setdefault(round((w[1] + w[3]) / 2.0 / max(1.0, 0.6 * mh)), []).append(w)
        parts = []
        for key in sorted(lines):
            parts.extend(order_words(lines[key], rtl_cell))
        return re.sub(r'\s+', ' ', ' '.join(_split_scripts(w[4]) for w in parts)).strip().replace('|', '/')

    out = ['| ' + ' | '.join(_CANON[c['type']] for c in order) + ' |',
           '|' + '---|' * len(order)]
    for row in data:
        out.append('| ' + ' | '.join(cell_text(row['cells'].get(id(c), [])) for c in order) + ' |')
    return out


def position_table(rows, carry=None):
    """Rebuilds the item table of one PDF page from word POSITIONS instead of
    word order. rows: list of lines, each a list of (x0, y0, x1, y1, text).
    The header line gives the column x-ranges; every word of the rows below is
    put in the column it physically sits under, and lines without numbers
    (wrapped descriptions) are joined to the nearest numbered row.

    carry: the (order, bounds, mh) structure returned for a page that DID
    have its own header, passed back in on the next page - a continuation
    page (the item table carries on from a previous page without repeating
    the header row) reuses that same column structure instead of giving up.

    Returns (index of the header line, markdown table lines, structure) where
    structure is the (order, bounds, mh) to carry to the next page, or None
    if this page has nothing usable (no header AND no carried structure, or a
    carried structure that matches no row here either)."""
    def line_text(r):
        return ' '.join(w[4] for w in r)

    hi = None
    for i, r in enumerate(rows):
        seq = header_sequence(line_text(r))
        if is_header_seq(seq) and len(seq) >= 3:
            hi = i
            break
    if hi is None:
        if carry is None:
            return None
        order, bounds, mh = carry
        table_lines = _extract_table_data(rows, order, bounds, mh, start_at=0)
        if table_lines is None:
            return None
        return -1, table_lines, carry

    def yc(r):
        return sum((w[1] + w[3]) / 2.0 for w in r) / len(r)

    heights = sorted(max(1.0, w[3] - w[1]) for r in rows for w in r)
    mh = heights[len(heights) // 2]

    # header band: neighbouring lines that also carry header words (bilingual / wrapped headers)
    band = {hi}
    for j in list(range(hi - 1, max(-1, hi - 4), -1)) + list(range(hi + 1, min(len(rows), hi + 4))):
        if abs(yc(rows[j]) - yc(rows[hi])) > 3.2 * mh:
            continue
        r = rows[j]
        if any(re.search(r'\d', w[4]) for w in r):
            continue
        kw = sum(1 for w in r if header_sequence(w[4]))
        # wrapped / bilingual header lines are mostly keywords; a sentence such as
        # "we would like to submit our best price for the following items" is not
        if kw / float(len(r)) >= 0.34 or (len(r) <= 3 and kw):
            band.add(j)
    last_band = max(band)

    # columns = horizontal clusters of the header words
    hw = sorted((w for j in band for w in rows[j]), key=lambda w: w[0])
    clusters = []
    gap = max(2.5, 0.35 * mh)
    for w in hw:
        if clusters and w[0] <= clusters[-1]['x1'] + gap:
            c = clusters[-1]
            c['x1'] = max(c['x1'], w[2])
            c['words'].append(w)
        else:
            clusters.append({'x0': w[0], 'x1': w[2], 'words': [w]})
    if len(clusters) < 3:
        return None
    for c in clusters:
        c['xc'] = (c['x0'] + c['x1']) / 2.0
        ordered = sorted(c['words'], key=lambda w: (round((w[1] + w[3]) / 2.0), w[0]))
        c['type'] = _cluster_type(' '.join(w[4] for w in ordered))
    types = [c['type'] for c in clusters]
    if os.environ.get('Q2PO_DEBUG'):
        for c in clusters:
            print('CLUSTER', round(c['x0']), round(c['x1']), c['type'], [w[4] for w in c['words']], file=sys.stderr)
    if 'desc' not in types or not ({'qty', 'price'} & set(types)):
        return None

    desc_x = sum(c['xc'] for c in clusters if c['type'] == 'desc') / types.count('desc')
    numc = [c['xc'] for c in clusters if c['type'] in ('qty', 'price', 'total')]
    rtl = desc_x > (sum(numc) / len(numc) if numc else 0)
    order = sorted(clusters, key=lambda c: -c['xc'] if rtl else c['xc'])
    seen = set()
    for c in order:                       # one qty / price / total column each; the first one wins
        if c['type'] in ('qty', 'price', 'total', 'unit'):
            if c['type'] in seen:
                c['type'] = 'other'
            else:
                seen.add(c['type'])

    # text columns (size, colour, material, code) sitting between the description
    # and the first number column are part of the item name
    ti = [k for k, c in enumerate(order) if c['type'] == 'desc']
    ni = [k for k, c in enumerate(order) if c['type'] in ('qty', 'price', 'total')]
    if ti and ni:
        for k in range(max(ti) + 1, min(ni)):
            if order[k]['type'] in ('other', 'code'):
                order[k]['type'] = 'desc'

    by_x = sorted(clusters, key=lambda c: c['xc'])
    # boundary = middle of the empty gap between two header clusters
    bounds = [(by_x[k]['x1'] + by_x[k + 1]['x0']) / 2.0 for k in range(len(by_x) - 1)]
    # a number column right next to the description: numbers are usually wider
    # than their header, descriptions start far from theirs, so measure where the
    # numbers really end and put the boundary just after them
    header_bounds = list(bounds)

    def header_col(w):
        x = (w[0] + w[2]) / 2.0
        k = 0
        while k < len(header_bounds) and x > header_bounds[k]:
            k += 1
        return by_x[k]

    for k in range(len(by_x) - 1):
        a, b = by_x[k], by_x[k + 1]
        pair = (a['type'], b['type'])
        if 'desc' in pair and ({'qty', 'price', 'total'} & set(pair)):
            ncol = a if b['type'] == 'desc' else b
            xs = []
            for r in rows[last_band + 1:last_band + 40]:
                lt = line_text(r)
                if xs and (TOTAL_ROW.search(ar_key(lt)) or TOTAL_ROW.search(lt)):
                    break                          # end of the item rows
                for w in r:
                    if '%' not in w[4] and num(w[4]) is not None and re.search(r'\d', w[4]) \
                       and header_col(w) is ncol:
                        xs.append(w[2] if ncol is a else w[0])
            if xs:
                edge = max(xs) if ncol is a else min(xs)
                bounds[k] = edge + 0.8 * mh if ncol is a else edge - 0.8 * mh
        elif 'desc' in pair and 'unit' in pair:
            # units sit right under their header; the description may run up to it
            bounds[k] = b['x0'] - 0.5 * mh if b['type'] == 'unit' else a['x1'] + 0.5 * mh
    if os.environ.get('Q2PO_DEBUG'):
        print('BOUNDS', [round(x) for x in bounds], [c['type'] for c in by_x], file=sys.stderr)

    def col_of(w):
        x = (w[0] + w[2]) / 2.0
        k = 0
        while k < len(bounds) and x > bounds[k]:
            k += 1
        return by_x[k]

    table_lines = _extract_table_data(rows, order, bounds, mh, start_at=last_band + 1)
    if table_lines is None:
        return None
    return hi, table_lines, (order, bounds, mh)


_LAM_ALEF = {'لا', 'لأ', 'لإ', 'لآ'}
_AR_LETTER = re.compile('[\u0621-\u064A\u066E-\u06D3]')


def _is_fragment(text):
    """A piece that is not a word by itself: a lam-alef ligature glyph or a lone Arabic letter."""
    t = norm(text)
    return t in _LAM_ALEF or (len(t) == 1 and bool(_AR_LETTER.match(t)))


def _glue_fragments(row):
    """PDFs often draw «لا» (and some joined letters) as separate glyphs, so the text comes out as
    «والمقاو لا ت» instead of «والمقاولات». Glue such a piece to its touching neighbours (right-to-left order)."""
    ws = sorted(row, key=lambda w: w[0])
    if len(ws) < 2 or not any(_is_fragment(w[4]) for w in ws):
        return row
    out, i = [], 0
    while i < len(ws):
        group = [ws[i]]
        j = i
        while j + 1 < len(ws):
            a, b = ws[j], ws[j + 1]
            h = max(1.0, min(a[3] - a[1], b[3] - b[1]))
            touching = (b[0] - a[2]) <= 0.45 * h
            both_arabic = _AR_LETTER.search(a[4]) and _AR_LETTER.search(b[4])
            if touching and both_arabic and (_is_fragment(a[4]) or _is_fragment(b[4])):
                group.append(b)
                j += 1
            else:
                break
        if len(group) > 1:
            text = ''.join(w[4] for w in sorted(group, key=lambda w: -w[0]))        # right-to-left
            out.append((min(w[0] for w in group), min(w[1] for w in group), max(w[2] for w in group),
                        max(w[3] for w in group), text))
        else:
            out.append(group[0])
        i = j + 1
    return out


def _pages_to_md(pages, min_chars=40):
    """Pages of words [(x0, y0, x1, y1, text)] -> Markdown. Shared by the PDF text layer and by OCR."""
    out = []
    total_chars = 0
    carry = None   # (order, bounds, mh) from the last page whose own header was found -
                   # a later page with no repeated header reuses it (a table split across pages)
    for words in pages:
        if not words:
            continue
        words = [(w[0], w[1], w[2], w[3], norm(w[4])) for w in words if norm(w[4])]
        words.sort(key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
        rows, cur, cur_y, cur_h = [], [], None, None
        for w in words:
            yc = (w[1] + w[3]) / 2.0
            h = max(1.0, w[3] - w[1])
            if cur and abs(yc - cur_y) <= 0.55 * max(h, cur_h):
                cur.append(w)
                cur_y = (cur_y * (len(cur) - 1) + yc) / len(cur)
            else:
                if cur:
                    rows.append(cur)
                cur, cur_y, cur_h = [w], yc, h
        if cur:
            rows.append(cur)
        rows = [_glue_fragments(r) for r in rows]
        table = None
        try:
            table = position_table(rows, carry=carry)
        except Exception:                                # noqa - the plain lines below still work
            table = None
        if table:
            carry = table[2]
        for i, r in enumerate(rows):
            r2 = order_words(r, _is_rtl(r))
            line = ' '.join(_split_scripts(w[4]) for w in r2)
            line = re.sub(r'\s+', ' ', line).strip()
            if line:
                out.append('\\' + line if line[0] in '#>' else line)   # keep '#' / '>' literal in Markdown
                total_chars += len(line)
            if table and table[0] == i:
                out.append('')
                out.extend(table[1])
                out.append('')
        if table and table[0] == -1:          # continuation page: no header line to interleave at
            out.append('')
            out.extend(table[1])
            out.append('')
        out.append('')
    if total_chars < min_chars:
        raise NeedsAI('scanned')
    return '\n'.join(out)


def pdf_to_md(path):
    """PDF -> Markdown. Text lines are rebuilt from word positions (RTL aware);
    the item table, when a header row is found, is additionally rebuilt as a
    Markdown table from the words' column positions (see position_table)."""
    import pymupdf
    doc = pymupdf.open(path)
    return _pages_to_md(page.get_text('words') for page in doc)


# --------------------------------------------------------------------------- damaged text + OCR
_ODD = re.compile('[\u0370-\u03FF\u00C0-\u00FF\u0100-\u024F\u0400-\u04FF]')      # Greek, accented Latin, Cyrillic
_ANY_LETTER = re.compile(r'[^\W\d_]', re.UNICODE)


def looks_garbled(text):
    """True when the text layer is damaged: a PDF whose fonts have no Unicode map comes out as Greek / accented-Latin
    junk («έηكΔ الΏοϬ») instead of Arabic. Such a file can only be read by OCR."""
    letters = _ANY_LETTER.findall(text or '')
    if len(letters) < 30:
        return False
    odd = sum(1 for ch in letters if _ODD.match(ch))
    return odd >= 8 and odd / len(letters) > 0.04


IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp')


_TLS = threading.local()          # per-call overrides used by the quote comparison: .lang (e.g. 'eng') and .pages ((first, last), 1-based)


def _ocr_settings():
    try:
        import config
        return config.TESSERACT_PATH, (getattr(_TLS, 'lang', None) or config.OCR_LANG), config.OCR_MODE
    except Exception:                                    # noqa - used as a stand-alone tool
        return '', (getattr(_TLS, 'lang', None) or 'ara+eng'), 'auto'


def ocr_languages():
    """The OCR languages to use (the configured ones that are really installed), or [] when OCR is not possible."""
    path, lang, mode = _ocr_settings()
    if mode == 'off':
        return []
    try:
        import pytesseract
        if path:
            pytesseract.pytesseract.tesseract_cmd = path
        installed = set(pytesseract.get_languages(config=''))
    except Exception:                                    # noqa - library or program missing
        return []
    return [l for l in lang.split('+') if l in installed]


def ocr_available():
    return bool(ocr_languages())


def _page_images(path):
    from PIL import Image
    import io
    if os.path.splitext(path)[1].lower() in IMAGE_EXT:
        img = Image.open(path)
        n = getattr(img, 'n_frames', 1)
        for i in range(min(n, 5)):
            img.seek(i)
            yield img.convert('RGB')
        return
    import pymupdf
    limit = getattr(_TLS, 'pages', None)
    for number, page in enumerate(pymupdf.open(path), 1):
        if limit and not (limit[0] <= number <= limit[1]):
            continue
        yield Image.open(io.BytesIO(page.get_pixmap(dpi=300).tobytes('png'))).convert('RGB')


def _ocr_words(img, lang):
    import pytesseract
    from PIL import ImageOps
    img = ImageOps.autocontrast(ImageOps.grayscale(img))
    d = pytesseract.image_to_data(img, lang=lang, config='--psm 6', output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d['text']):
        t = (t or '').strip()
        try:
            conf = float(d['conf'][i])
        except (TypeError, ValueError):
            conf = -1
        if t and conf >= 25:
            words.append((d['left'][i], d['top'][i], d['left'][i] + d['width'][i], d['top'][i] + d['height'][i], t))
    return words


def ocr_to_md(path):
    """Image or PDF -> Markdown by optical character recognition. The recognised words go through the very same
    row / column logic as a PDF text layer, so the items table is rebuilt the same way."""
    langs = ocr_languages()
    if not langs:
        raise NeedsAI('ocr-unavailable')
    lang = '+'.join(langs)
    return _pages_to_md((_ocr_words(img, lang) for img in _page_images(path)), min_chars=20)


class MissingLibrary(Exception):
    pass


def markitdown_md(path):
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise MissingLibrary('markitdown')
    res = MarkItDown().convert(path)
    return res.text_content


# ---- built-in readers (standard library only): work even when the optional
# ---- libraries were not installed, and keep Word/Excel tables intact.
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _md_cell(t):
    return re.sub(r'\s+', ' ', (t or '').replace('|', '/')).strip()


def _md_table(rows):
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [''] * (width - len(r)) for r in rows]
    out = ['| ' + ' | '.join(_md_cell(c) for c in rows[0]) + ' |',
           '|' + '---|' * width]
    for r in rows[1:]:
        out.append('| ' + ' | '.join(_md_cell(c) for c in r) + ' |')
    return out


# ----------------------------------------------------------------------------- limits for files that arrive from OUTSIDE the company
class UnsafeFile(ValueError):
    """A file that would use an abnormal amount of memory or time (a «zip bomb»); it is refused before anything is unpacked."""


MAX_UNPACKED_MB, MAX_MEMBERS, MAX_RATIO = 100, 2000, 1000
MAX_SHEET_ROWS, MAX_SHEET_COLS, MAX_SHEETS = 20000, 200, 30


def check_zip_safe(path):
    """Looks only at the file's table of contents (nothing is unpacked): total unpacked size, number of parts, and absurd compression ratios."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile:
        return                                                # not a zip at all: the normal readers will reject it in their own way
    total = sum(i.file_size for i in infos)
    if len(infos) > MAX_MEMBERS or total > MAX_UNPACKED_MB * 1024 * 1024:
        raise UnsafeFile("الملف يحتوي بيانات أكبر من المعقول (%d ميجابايت بعد الفك)." % (total // (1024 * 1024)))
    for i in infos:
        if i.file_size > 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
            raise UnsafeFile("الملف مضغوط بشكل غير طبيعي ولا يُقرأ للحماية.")


def docx_to_md(path):
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    body = root.find(_W + 'body')

    def ptext(p):
        parts = []
        for node in p.iter():
            if node.tag == _W + 't' and node.text:
                parts.append(node.text)
            elif node.tag in (_W + 'tab',):
                parts.append(' ')
            elif node.tag in (_W + 'br', _W + 'cr'):
                parts.append(' ')
        return ''.join(parts)

    out = []
    for el in body:
        if el.tag == _W + 'p':
            t = ptext(el).strip()
            if t:
                out.append(t)
        elif el.tag == _W + 'tbl':
            rows = []
            for tr in el.iter(_W + 'tr'):
                cells = []
                for tc in tr.findall(_W + 'tc'):
                    cells.append(' '.join(ptext(p) for p in tc.iter(_W + 'p')).strip())
                rows.append(cells)
            out.append('')
            out.extend(_md_table(rows))
            out.append('')
    return '\n'.join(out)


def _col_index(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_to_md(path):
    import zipfile
    import xml.etree.ElementTree as ET
    out = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(_S + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(_S + 't')))
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = {r.get('Id'): r.get('Target') for r in rels}
        for n_sheet, sh in enumerate(wb.find(_S + 'sheets')):
            if n_sheet >= MAX_SHEETS:
                break
            t = target.get(sh.get(_R + 'id'), '')
            t = t.lstrip('/')
            if not t.startswith('xl/'):
                t = 'xl/' + t
            if t not in names:
                continue
            root = ET.fromstring(z.read(t))
            rows = []
            for n_row, row in enumerate(root.iter(_S + 'row')):
                if n_row >= MAX_SHEET_ROWS:
                    break
                cells = {}
                for c in row.findall(_S + 'c'):
                    if _col_index(c.get('r') or 'A1') >= MAX_SHEET_COLS:
                        continue
                    typ = c.get('t')
                    v = c.find(_S + 'v')
                    if typ == 'inlineStr':
                        val = ''.join(x.text or '' for x in c.iter(_S + 't'))
                    elif v is None or v.text is None:
                        continue
                    elif typ == 's':
                        val = shared[int(v.text)]
                    elif typ in ('str', 'e'):
                        val = v.text
                    elif typ == 'b':
                        val = 'TRUE' if v.text == '1' else 'FALSE'
                    else:
                        try:
                            f = float(v.text)
                            val = fmt_num(round(f, 6))
                        except ValueError:
                            val = v.text
                    cells[_col_index(c.get('r'))] = val
                if cells:
                    rows.append([cells.get(i, '') for i in range(max(cells) + 1)])
            # lines that are not part of the table (title, supplier, quote no.)
            # read better as plain text: rows with a single filled cell
            table = []
            for r in rows:
                filled = [x for x in r if str(x).strip()]
                if len(filled) <= 1 and not table:
                    out.append(' '.join(str(x) for x in filled))
                elif len(filled) <= 1 and table:
                    out.extend(_md_table(table))
                    table = []
                    out.append(' '.join(str(x) for x in filled))
                else:
                    table.append([str(x) for x in r])
            if table:
                out.extend(_md_table(table))
            out.append('')
    return '\n'.join(out)


def html_to_md(path):
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.lines, self.rows, self.row, self.cell, self.in_cell, self.buf = [], [], None, [], False, []

        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self.row = []
            elif tag in ('td', 'th'):
                self.in_cell, self.cell = True, []
            elif tag in ('br', 'p', 'div') and not self.in_cell:
                self.flush()

        def handle_endtag(self, tag):
            if tag in ('td', 'th') and self.row is not None:
                self.row.append(''.join(self.cell).strip())
                self.in_cell = False
            elif tag == 'tr' and self.row is not None:
                self.rows.append(self.row)
                self.row = None
            elif tag == 'table':
                self.flush()
                self.lines.extend(_md_table(self.rows))
                self.rows = []
            elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'li'):
                self.flush()

        def handle_data(self, d):
            (self.cell if self.in_cell else self.buf).append(d)

        def flush(self):
            t = re.sub(r'\s+', ' ', ''.join(self.buf)).strip()
            if t:
                self.lines.append(t)
            self.buf = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        p = P()
        p.feed(f.read())
        p.flush()
    return '\n'.join(p.lines)


def convert_to_md(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.md', '.txt'):
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(), 'text'
    if ext in IMAGE_EXT:
        if ocr_available():
            return ocr_to_md(path), 'ocr'
        raise NeedsAI('image')
    if ext in ('.docx', '.xlsx', '.xlsm'):
        check_zip_safe(path)                                # raises UnsafeFile: NOT retried with another reader
    builtin = {'.docx': docx_to_md, '.xlsx': xlsx_to_md, '.xlsm': xlsx_to_md, '.html': html_to_md, '.htm': html_to_md}
    if ext in builtin:
        try:
            md = builtin[ext](path)
            if len(md.strip()) >= 20:
                return md, 'builtin'
        except Exception:                                # noqa - fall back to markitdown below
            pass
        return markitdown_md(path), 'markitdown'
    if ext == '.pdf':
        try:
            md = pdf_to_md(path)
        except NeedsAI:                                  # no text layer at all (a scan)
            if ocr_available():
                return ocr_to_md(path), 'ocr'
            raise
        except ImportError:
            md = None
        if md is not None:
            if looks_garbled(md):                        # fonts without a Unicode map: the text is junk
                if ocr_available():
                    return ocr_to_md(path), 'ocr'
                raise NeedsAI('garbled')
            return md, 'pymupdf'
        try:
            return markitdown_md(path), 'markitdown'
        except MissingLibrary:
            raise MissingLibrary('pymupdf')
    return markitdown_md(path), 'markitdown'


def missing_lib_message(lib):
    here_venv = os.path.join(HERE, 'venv')
    where = 'venv' if os.path.normcase(sys.executable).startswith(os.path.normcase(here_venv)) else sys.executable
    return ('مكتبة القراءة "%s" غير مثبتة في بايثون المستخدم (%s). '
            'شغّل الملف install_tool.bat الموجود في المجلد: %s مرة واحدة (يحتاج اتصالاً بالإنترنت)، ثم أعد الاستيراد. '
            'ملفات Word وExcel تُقرأ الآن بدون هذه المكتبة؛ أما PDF فيحتاجها.') % (lib, where, HERE)


# ----------------------------------------------------------------------------- parsing
# Longer phrases win over shorter ones at the same position (see header_sequence),
# so compound headers such as "السعر الإجمالي" are read as TOTAL, not PRICE, and
# "سعر الوحدة" as PRICE, not UNIT.
COLS = {
    'total': ['السعر الاجمالي', 'اجمالي السعر', 'القيمه الاجماليه', 'المبلغ الاجمالي', 'الاجمالي', 'اجمالي',
              'جمالي', 'المجموع', 'القيمه', 'قيمه', 'المبلغ', 'total price', 'total amount', 'line total',
              'total', 'amount', 'value', 'net amount'],
    'price': ['سعر الوحده', 'سعر الافراد', 'السعر الافرادي', 'سعر الفئه', 'السعر', 'سعر', 'الفئه',
              'unit price', 'unit cost', 'unit rate', 'price', 'rate'],
    'qty': ['الكميه', 'كميه', 'العدد', 'qty', 'quantity', "q'ty", 'qnty'],
    'unit': ['حجم العبوه', 'العبوه', 'الوحده', 'وحده', 'unit', 'uom', 'u/m', 'packing'],
    'code': ['رقم الصنف', 'كود الصنف', 'الكود', 'كود', 'الرمز', 'item code', 'item no', 'part no', 'code', 'sku'],
    'desc': ['اسم الصنف', 'وصف الصنف', 'بيان الصنف', 'الصنف', 'البيان', 'بيان', 'البند', 'الوصف', 'وصف',
             'المواصفات', 'المادة', 'الماده', 'الاسم', 'item description', 'description', 'item', 'product',
             'details', 'particulars', 'material', 'scope'],
    'serial': ['م', '#', 'no', 'no.', 'sr', 'sl', 'serial', 'الرقم', 'رقم', 'ت', 's.n', 'sn'],
}
_SHORT = ('no', 'sr', 'sl', '#', 'sn')
TOTAL_ROW = re.compile(r'(جمالي|مجموع(?![هة])|ضريب|قيمه مضافه|\bsubtotal\b|\bsub total\b|\bgrand total\b|^total\b|\bvat\b|\btax\b|خصم|\bdiscount\b|\bnet (?:amount|total|value|price|payable)\b|^net$)', re.I)
_CUR = re.compile(r'(?i)^(sar|sr|ريال|ر\.س|﷼|ر\.س\.)$')


def header_sequence(text):
    """Column types in order of appearance inside a header text (line or cell)."""
    k = ar_key(text)
    found = []
    for t, kws in COLS.items():
        for kw in kws:
            kk = ar_key(kw)
            if len(kk) <= 2 or kk in _SHORT:
                for m in re.finditer(r'(?<![^\W_])' + re.escape(kk) + r'(?![^\W_])', k):
                    found.append((m.start(), t, m.end()))
            else:
                for m in re.finditer(re.escape(kk), k):
                    found.append((m.start(), t, m.end()))
    found.sort(key=lambda x: (x[0], -x[2]))
    seq, last_end = [], -1
    for start, t, end in found:
        if start < last_end:
            continue            # overlapped by an earlier / longer keyword
        last_end = end
        if seq and seq[-1] == t:
            continue
        seq.append(t)
    return seq


def col_type(cell):
    seq = header_sequence(cell)
    return seq[0] if seq else None


def is_header_seq(seq):
    return 'desc' in seq and ('qty' in seq or 'price' in seq)


def clean_md_line(line):
    line = line.replace('<br>', ' ').replace('<br/>', ' ').replace('\\#', '\u0001')
    line = re.sub(r'^\s*(?:#{1,6}\s+|>\s*)', '', line)
    line = re.sub(r'`+|(?<![\w.])\*+|\*+(?![\w.])', ' ', line).replace('\u0001', '#')
    return norm(line)


_EMPTY_CELL = re.compile(r'^(?:nan|none|null|unnamed: ?\d+)$', re.I)


def pipe_cells(line):
    cells = [c.strip() for c in line.strip().strip('|').split('|')]
    return [('' if _EMPTY_CELL.match(c) else c) for c in cells]


def _close(a, b):
    return abs(a - b) <= max(0.01 * abs(b), 0.05)


def _is_int(v):
    return abs(v - round(v)) < 1e-9


def reconcile(vals, nums, qty_first=None):
    """Makes qty * price == total true when the numbers of the row allow it.
    vals: dict with the (possibly wrong) qty/price/total taken from the header
    mapping; nums: every numeric value found in the row's number columns, in
    reading order. Fixes shifted columns (price read as qty, total as price...)
    and extra numeric columns (VAT per line, discount, codes)."""
    q, p, t = vals.get('qty'), vals.get('price'), vals.get('total')
    if q is not None and p is not None and t is not None and _close(q * p, t):
        return vals
    cands = []
    n = len(nums)
    for ci in range(n):
        c = nums[ci]
        if c <= 0:
            continue
        for ai in range(n):
            for bi in range(ai + 1, n):
                if ci in (ai, bi):
                    continue
                a, b = nums[ai], nums[bi]
                if a <= 0 or b <= 0 or not _close(a * b, c):
                    continue
                score = 0
                if t is not None and _close(c, t):
                    score += 3
                if q is not None and (_close(a, q) or _close(b, q)):
                    score += 2
                if p is not None and (_close(a, p) or _close(b, p)):
                    score += 2
                score += c / (max(nums) or 1)          # prefer the biggest consistent total
                cands.append((score, ai, bi, ci))
    if not cands:
        return vals
    cands.sort(reverse=True)
    _, ai, bi, ci = cands[0]
    a, b, c = nums[ai], nums[bi], nums[ci]
    # a comes before b in reading order: the header order decides which one is the
    # quantity; without a usable header the whole number is taken as the quantity.
    if qty_first is None:
        if _is_int(a) != _is_int(b):
            qq, pp = (a, b) if _is_int(a) else (b, a)
        else:
            qq, pp = (a, b) if a <= b else (b, a)
    else:
        qq, pp = (a, b) if qty_first else (b, a)
    out = dict(vals)
    out.update(qty=qq, price=pp, total=c, fixed=True)
    return out


def _qty_first(cols):
    if 'qty' in cols and 'price' in cols:
        return cols.index('qty') < cols.index('price')
    return None


def parse_row_semantic(tokens, cols):
    """tokens/cols are in the same (reading) order."""
    toks = [t for t in tokens if t and not _CUR.match(t)]
    if not toks:
        return None
    di = cols.index('desc')
    after = cols[di + 1:]
    before = cols[:di]
    n_num_cols = sum(1 for c in after if c in ('qty', 'price', 'total'))

    # tail of the row: numbers plus (at most one per unit column) short words
    vals = {}
    nums = []
    end = len(toks)
    words_allowed = sum(1 for c in after if c in ('unit', 'code', 'other'))
    while end > 0:
        t = toks[end - 1]
        v = num(t)
        if v is not None and len(nums) < n_num_cols + 3:
            nums.insert(0, v)
            end -= 1
        elif v is None and words_allowed > 0 and len(t) <= 12 and nums:
            if 'unit' in after and 'unit' not in vals:
                vals['unit'] = t
            words_allowed -= 1
            end -= 1
        else:
            break
    if not nums:
        return None
    # strict mapping by header order on the numeric tail
    num_cols = [c for c in after if c in ('qty', 'price', 'total')]
    if len(nums) >= len(num_cols):
        for c, v in zip(num_cols, nums[len(nums) - len(num_cols):]):
            vals[c] = v
    else:
        for c, v in zip(num_cols[len(num_cols) - len(nums):], nums):
            vals[c] = v
    vals = reconcile(vals, nums, _qty_first(cols))
    # a unit word glued between the description and the numbers but not recognised above
    start = 0
    for c in before:
        if c == 'serial' and start < end and re.fullmatch(r'\d{1,3}[.)\-]?', toks[start]):
            start += 1
        elif c == 'code' and start < end:
            start += 1
    desc = ' '.join(toks[start:end]).strip()
    if not desc:
        return None
    vals['name'] = desc
    return vals


def _split_merged_cell(text, types):
    """A converted table cell may hold several columns ("Pail 15" under "Unit Qty",
    "1 Paint ..." under "# Description"): split it by the header's column types."""
    toks = text.split()
    out = {}
    num_types = [t for t in types if t in ('qty', 'price', 'total')]
    # trailing numbers -> numeric columns (right to left)
    while toks and num_types and num(toks[-1]) is not None:
        out[num_types.pop()] = toks.pop()
    if 'serial' in types and toks and re.fullmatch(r'\d{1,3}[.)\-]?', toks[0]) and len(toks) > 1:
        toks = toks[1:]
    rest = ' '.join(toks)
    if 'desc' in types:
        out['desc'] = rest
    elif 'unit' in types:
        out['unit'] = rest
    elif rest and num_types and num(rest) is not None:
        out[num_types[0]] = rest
    return out


def parse_row_cells(cells, cols):
    """cols: for every cell the list of column types its header holds."""
    vals = {}
    nums = []
    for types, t in zip(cols, cells):
        t = clean_md_line(t)
        if len(types) > 1:
            parts = _split_merged_cell(t, types)
        else:
            parts = {types[0]: t}
        for c, v in parts.items():
            if c in ('qty', 'price', 'total'):
                n = None if '%' in v else num(v)
                if n is None:
                    amounts = [(tok, num(tok)) for tok in v.split() if '%' not in tok and num(tok) is not None]
                    if len(amounts) == 1:
                        n = amounts[0][1]
                        rest = ' '.join(tok for tok in v.split() if tok != amounts[0][0]
                                        and not re.fullmatch(r'(?i)(sar|sr|s\.r|ر\.س|ريال|﷼)', tok))
                        if c == 'qty' and rest and not vals.get('unit'):
                            vals['unit'] = rest
                if n is not None:
                    vals[c] = n
                    nums.append(n)
            elif c == 'desc':
                vals['name'] = (vals['name'] + ' ' + v).strip() if vals.get('name') else v
            elif c == 'unit':
                vals['unit'] = v
            elif c == 'other':
                n = None if '%' in v else num(v)               # «خصم 10%» must not be mistaken for an amount
                if n is not None:
                    nums.append(n)
    if not vals.get('name'):
        return None
    flat = [c for types in cols for c in types]
    return reconcile(vals, nums, _qty_first(flat))


def parse_row_trailing(tokens):
    """Fallback without header: trailing numbers -> qty/price/total."""
    toks = [t for t in tokens if t and not _CUR.match(t)]
    nums = []
    i = len(toks)
    while i > 0 and num(toks[i - 1]) is not None and len(nums) < 4:
        nums.insert(0, num(toks[i - 1]))
        i -= 1
    if len(nums) < 2 or i == 0:
        return None
    vals = {}
    if len(nums) >= 3:
        vals = reconcile({}, nums)
        if not vals.get('fixed'):
            return None
    else:
        vals.update(qty=nums[0], price=nums[1])
    rest = toks[:i]
    if rest and re.fullmatch(r'\d{1,3}[.)\-]?', rest[0]) and len(rest) > 1:
        rest = rest[1:]
    name = ' '.join(rest).strip()
    if not name:
        return None
    vals['name'] = name
    return vals


# ----------------------------------------------------------------------------- supplier
_COMPANY = re.compile(r'(شركة|شركه|مؤسسة|مؤسسه|مصنع|مكتب|معرض|مجموعة|مجموعه|متجر|محلات|مستودع|\bCo\.|Company|Ltd|Limited|Trading|LLC|\bEst\.|Establishment|Contracting|Factory|Group|Corporation|Corp\.?|Enterprises|Industries|Supplies)', re.I)
_CUSTOMER = re.compile(r'(السادة|السيد|المحترم|العميل|عميل|customer|client|bill\s*to|ship\s*to|attn|attention|لعناية|^\s*to\b|^\s*(?:الى|إلى)\b|dear)', re.I)
_NOISE = re.compile(
    r'(عرض\s*سعر|عرض\s*اسعار|quotation|quote|proforma|فاتور|invoice|السجل|سجل\s*تجاري|الرقم\s*الضريبي|'
    r'ضريب|\bvat\b|\btax\b|\bc\.?r\.?\b|المشروع|project|التاريخ|\bdate\b|هاتف|جوال|فاكس|\btel\b|phone|mobile|\bfax\b|'
    r'e-?mail|@|www\.|http|ص\.?\s*ب\b|p\.?\s*o\.?\s*box|العنوان|address|صفحة|\bpage\b|البيان|الكمية|الإجمالي|الاجمالي|total|شروط|terms)', re.I)
_LABEL_SUP = re.compile(r'^(?:اسم\s*)?(?:المورد|البائع|الشركة\s*الموردة|مقدم\s*العرض|supplier(?:\s*name)?|vendor(?:\s*name)?|seller)\s*(?:[:：/-]\s*|\s+)(.+)$', re.I)
_NEXT_LABEL = re.compile(r'\s+(?:quotation|quote|estimate|date|no\.|ref\b|tel\b|phone|رقم|التاريخ|تاريخ)\b.*$', re.I)
_TITLE_WORDS = re.compile(r'(عرض\s*سعر|عرض\s*اسعار|عرض\s*أسعار|quotation|estimate|proforma)', re.I)


_MONTHS = {
    'يناير': 1, 'فبراير': 2, 'مارس': 3, 'ابريل': 4, 'أبريل': 4, 'مايو': 5, 'يونيو': 6, 'يوليو': 7,
    'اغسطس': 8, 'أغسطس': 8, 'سبتمبر': 9, 'اكتوبر': 10, 'أكتوبر': 10, 'نوفمبر': 11, 'ديسمبر': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9,
    'oct': 10, 'nov': 11, 'dec': 12}


def find_date(lines):
    """Quotation date: a labelled date first ("Date: 9/20/2026", "التاريخ ..."),
    else the first full date in the letter-head, else "20 سبتمبر, 2026"."""
    for l in lines:
        m = re.search(r'(?:التاريخ|تاريخ|date)\s*[:：]?\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})', l, re.I)
        if m:
            return m.group(1)
    for pat in (r'(\d{1,2}[/\-.]\d{1,2}[/\-.](?:19|20)\d{2})(?!\d)',
                r'(?<!\d)((?:19|20)\d{2}[/\-.]\d{1,2}[/\-.]\d{1,2})(?!\d)'):
        for l in lines[:30]:
            m = re.search(pat, l)
            if m:
                return m.group(1)
    names = '|'.join(sorted(_MONTHS, key=len, reverse=True))
    for l in lines[:40]:
        m = re.search(r'(\d{1,2})\s+(' + names + r')[a-z]*\.?\s*,?\s*(\d{4})', l, re.I)
        if m:
            return '%02d/%02d/%s' % (int(m.group(1)), _MONTHS[m.group(2).lower()], m.group(3))
    return ''


def _compact(s):
    return re.sub(r'[\W_]+', '', ar_key(s))


def find_supplier(plain, known=(), exclude=()):
    ex = [_compact(e) for e in exclude if len(_compact(e)) >= 4]

    def excluded(line):
        c = _compact(line)
        return any(e in c or (len(c) >= 4 and c in e) for e in ex)

    # 1) a supplier already kept in the workbook list appears in the document
    doc = _compact(' '.join(plain))
    for name in sorted(known, key=lambda s: -len(_compact(s))):
        k = _compact(name)
        if len(k) >= 4 and k in doc and not any(k in e or e in k for e in ex):
            return name.strip()
    # 2) explicit label  "المورد: ..." / "Supplier: ..."
    for l in plain[:60]:
        m = _LABEL_SUP.match(l)
        if m:
            v = _NEXT_LABEL.sub('', m.group(1)).strip(' :|-')
            if v and not excluded(v) and not re.fullmatch(r'[\d\s/\-.]+', v):
                return v
    # 3) scored letter-head (top) / footer (bottom) lines
    n = len(plain)
    idx = list(range(min(15, n))) + list(range(max(15, n - 10), n))
    best, best_score = '', -99
    for i in idx:
        l = re.split(r'\s[|｜]\s|\s{3,}', plain[i])[0]
        l = re.sub(r'\s+', ' ', _TITLE_WORDS.sub(' ', l)).strip(' :|-')
        if not l or len(l) > 90 or excluded(l) or _CUSTOMER.search(l):
            continue
        has_company = bool(_COMPANY.search(l))
        if _NOISE.search(l) and not has_company:
            continue
        if len(re.findall(r'[A-Za-z\u0600-\u06FF]', l)) < 4:
            continue
        score = 6 if has_company else 0
        if _NOISE.search(l):
            score -= 3
        if i < 5:
            score += 3 - i * 0.5
        if re.search(r'\d{3,}', l):
            score -= 3
        if re.search(r'\d{1,2}:\d{2}|\b[AP]M\b|https?:|\.php', l):   # browser print header / footer
            score -= 8
        if i >= 15:
            score -= 1
        if score > best_score:
            best, best_score = l, score
    return best if best_score >= 1 else ''


def fix_quote_no(q):
    """'-2026-1025Q' (bidi-reversed 'Q-2026-1025') -> 'Q-2026-1025'."""
    m = re.fullmatch(r'-([0-9][0-9\-/_.]*?)-?([A-Za-z]+)', q or '')
    if m:
        return m.group(2) + '-' + m.group(1).strip('-')
    return (q or '').strip('.-/_')


def fix_arabic_pdf_text(s):
    """Some PDF generators store lam-alef ligatures in visual order, so text is
    extracted as 'اإلجمالي' / 'األول' instead of 'الإجمالي' / 'الأول'."""
    return re.sub('ا([أإآ])ل', r'ال\1', s)


def parse_quote(md, known=(), exclude=()):
    res = {'supplier': '', 'quote_no': '', 'quote_date': '', 'items': [], 'notes': []}
    md = fix_arabic_pdf_text(md)
    lines = [clean_md_line(l) if not l.strip().startswith('|') else l.strip() for l in md.splitlines()]
    plain = [clean_md_line(' '.join(pipe_cells(l))) if l.startswith('|') else l for l in lines]
    plain = [l for l in plain if not re.match(r'^(content from the zip file|file:\s|\{"num_pages")', l, re.I)]

    # ---- header data
    res['supplier'] = find_supplier(plain, known, exclude)
    qlabel = (r'(?:رقم\s*(?:عرض\s*السعر|العرض|المرجع)|عرض\s*سعر\s*رقم|رقم\s*عرض|quotation\s*(?:no|number|#|ref)'
              r'|quote\s*(?:no|number|#)|estimate\s*(?:no|number|#)|ref(?:erence)?\s*(?:no|#)|offer\s*(?:no|number))')
    idtok = r'(-?[A-Za-z0-9][A-Za-z0-9\-/_.]*)'
    qpat = re.compile(qlabel + r'\s*[:：#.]*\s*' + idtok, re.I)
    qpat_before = re.compile(r'(?:^|\s)([A-Za-z]*\d[A-Za-z0-9\-/_.]*)\s+' + qlabel, re.I)
    qpat_end = re.compile(qlabel + r'\s*[:：#.]*\s*$', re.I)
    top = plain[:60]
    for k, l in enumerate(top):
        m = qpat.search(l)
        if m and re.search(r'\d', m.group(1)):
            res['quote_no'] = fix_quote_no(m.group(1))
            break
        m = qpat_before.search(l)                      # "04089 Estimate No"
        if m:
            res['quote_no'] = fix_quote_no(m.group(1))
            break
        if qpat_end.search(l) and k + 1 < len(top):    # label at the end, value on the next line
            m = re.match(r'\s*([A-Za-z]*\d[A-Za-z0-9\-/_.]*)', top[k + 1])
            if m:
                res['quote_no'] = fix_quote_no(m.group(1))
                break
        m2 = re.search(r':\s*رقم\s*(?:عرض\s*السعر|العرض)\s*([A-Za-z0-9][A-Za-z0-9\-/_.]*)', l)   # reversed extraction
        if m2:
            res['quote_no'] = m2.group(1)
            break
        m3 = re.search(r'(?:عرض\s*السعر|العرض)\s*[:：]?\s*([A-Za-z][A-Za-z0-9\-/_.]*\d[A-Za-z0-9\-/_.]*)', l)
        if m3:
            res['quote_no'] = m3.group(1)
            break
    res['quote_date'] = find_date(top)

    # ---- items: markdown pipe tables first
    items = []
    i = 0
    while i < len(lines):
        if lines[i].startswith('|'):
            j = i
            block = []
            while j < len(lines) and lines[j].startswith('|'):
                block.append(lines[j])
                j += 1
            rows = []
            for b in block:
                cells = pipe_cells(b)
                if all(re.fullmatch(r':?-{2,}:?', c) or c == '' for c in cells):
                    continue
                rows.append(cells)
            hdr_idx, cols = None, None
            for ri, cells in enumerate(rows[:20]):
                multi = [header_sequence(clean_md_line(c)) for c in cells]
                seq = [t for types in multi for t in types]
                if is_header_seq(seq) and len(seq) >= 3:
                    hdr_idx, cols = ri, [types or ['other'] for types in multi]
                    break
            if cols:
                for cells in rows[hdr_idx + 1:]:
                    # a totals row is recognised by its label (description / serial
                    # cells), not by words elsewhere such as a "Sales Tax 15%" column
                    label = ' '.join(clean_md_line(c) for c, types in zip(cells, cols)
                                     if 'desc' in types or 'serial' in types)
                    if not label.strip():
                        label = ' '.join(clean_md_line(c) for c in cells if num(clean_md_line(c)) is None)
                    if TOTAL_ROW.search(ar_key(label)) or TOTAL_ROW.search(label):
                        continue
                    v = parse_row_cells(cells, cols)
                    if v:
                        items.append(v)
            i = j
        else:
            i += 1

    # ---- plain text lines (PDF text)
    if not items:
        cols = None
        for l in plain:
            if not l:
                continue
            seq = header_sequence(l)
            if cols is None:
                if is_header_seq(seq) and len(seq) >= 3:
                    cols = seq
                continue
            if TOTAL_ROW.search(ar_key(l)) or TOTAL_ROW.search(l):
                if items:
                    break
                continue
            toks = l.split(' ')
            v = parse_row_semantic(toks, cols)
            if v is None:
                v = parse_row_trailing(toks)
            if v:
                items.append(v)
            elif items:
                # continuation line of the previous description (wrapped text)
                if not any(num(t) is not None for t in toks[:1]) and len(toks) < 8 and num(toks[-1]) is None:
                    items[-1]['name'] += ' ' + l
        if not items:                 # no header at all: try trailing-number rows
            for l in plain:
                if TOTAL_ROW.search(ar_key(l)):
                    continue
                v = parse_row_trailing(l.split(' '))
                if v:
                    items.append(v)
            if items:
                res['notes'].append('لم يُعثر على ترويسة جدول؛ استُخرجت الأصناف من الأسطر ذات الأرقام — راجعها بعناية.')

    for k, it in enumerate(items):
        m = re.match(r'^(\d{1,3})[.)\-]?\s+(.+)$', it['name'])
        if m and int(m.group(1)) == k + 1:            # leading serial number left in the description
            it['name'] = m.group(2)
        q, p, t = it.get('qty'), it.get('price'), it.get('total')
        if q is None and p is not None and t is not None and p:
            q = t / p
        if p is None and q and t is not None:
            p = t / q
        if q is None:
            q = 1.0
        if p is None:
            p = 0.0
        if t is not None and abs(q * p - t) > max(0.02 * abs(t), 0.05):
            res['notes'].append('تنبيه: إجمالي الصنف «%s» لا يساوي الكمية × السعر — راجعه.' % it['name'][:40])
        res['items'].append({'name': it['name'], 'unit': it.get('unit', ''), 'qty': q, 'price': p})
    if res['items'] and any(it['price'] == 0 for it in res['items']):
        res['notes'].append('بعض الأصناف بلا سعر منفصل (قد يكون العرض بسعر إجمالي واحد للنظام) — أدخل الأسعار يدوياً.')
    if any(it.get('fixed') for it in items):
        res['notes'].append('صُحّح توزيع الأرقام (الكمية/السعر/الإجمالي) في بعض الأصناف تلقائياً بمطابقة الكمية × السعر = الإجمالي — راجعها.')
    return res


def local_confidence(res):
    """Confidence of a parse_quote() result, using only signals it already
    records: 'none' (no items), 'low' (no table header was found at all -
    items came from the bare trailing-numbers fallback), 'medium' (some
    item totals didn't match qty*price), 'high' (clean header-based parse)."""
    if not res or not res['items']:
        return 'none'
    if any(n.startswith('لم يُعثر على ترويسة') for n in res['notes']):
        return 'low'
    if any(it['price'] == 0 for it in res['items']):
        return 'low'
    if any(n.startswith('تنبيه: إجمالي الصنف') for n in res['notes']):
        return 'medium'
    if not res.get('supplier'):
        return 'medium'
    return 'high'


# ----------------------------------------------------------------------------- Excel fallback
def _split_text_line(line):
    """'كابل نحاس 3×2.5 ملم لفة 20 185.00 3,700.00' -> ['كابل نحاس 3×2.5 ملم', 'لفة', 20, 185, 3700]"""
    toks = line.split(' ')
    tail = []
    i = len(toks)
    while i > 0:
        t = toks[i - 1]
        v = num(t)
        if v is not None:
            tail.insert(0, v)
        elif tail and len(t) <= 12 and not any(isinstance(x, str) for x in tail):
            tail.insert(0, t)
        else:
            break
        i -= 1
    if not any(not isinstance(x, str) for x in tail):
        return [line]
    head = ' '.join(toks[:i]).strip()
    return ([head] if head else []) + tail


def md_table_to_xlsx(md, path):
    """Writes the converted quotation into a plain right-to-left .xlsx so the user
    can copy the figures by hand when automatic extraction was not reliable:
    Markdown tables keep their cells; text lines are split into a description
    cell followed by one cell per trailing number (qty / price / total...).
    Returns True when a file was written."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = 'Quotation'
    ws.sheet_view.rightToLeft = True
    ws.cell(row=1, column=1, value='عرض السعر محوَّلاً لنسخ البيانات يدوياً — راجع الأرقام قبل نسخها إلى أمر الشراء').font = Font(bold=True)
    row = 3
    wrote_any = False
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if raw.startswith('|'):
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = pipe_cells(lines[i].strip())
                i += 1
                if all(re.fullmatch(r':?-{2,}:?', c) or c == '' for c in cells):
                    continue
                for ci, val in enumerate(cells, start=1):
                    v = clean_md_line(val)
                    n = num(v)
                    ws.cell(row=row, column=ci, value=n if n is not None else v)
                row += 1
                wrote_any = True
            row += 1
            continue
        i += 1
        if not raw or re.match(r'^(content from the zip file|## file:|\{"num_pages")', raw, re.I):
            continue
        for ci, val in enumerate(_split_text_line(fix_arabic_pdf_text(clean_md_line(raw))), start=1):
            ws.cell(row=row, column=ci, value=val)
        row += 1
        wrote_any = True
    if not wrote_any:
        return False
    for col in ws.columns:
        letter = col[0].column_letter
        width = max((len(str(c.value)) for c in col[2:] if c.value is not None), default=10)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 60)
        for c in col:
            c.alignment = Alignment(horizontal='right', vertical='center', wrap_text=False)
    wb.save(path)
    return True


# ----------------------------------------------------------------------------- AI (Claude) mode
def load_config():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(HERE, 'config.ini'), encoding='utf-8')
    return cp


AI_PROMPT = (
    "You read supplier quotations written in Arabic and/or English (text, tables, scans, stamps). "
    "Return ONLY one JSON object, no prose, with this exact shape:\n"
    '{"supplier": "name of the company that issued the quotation", "quote_no": "quotation number", '
    '"quote_date": "yyyy-mm-dd or empty", "currency": "SAR if stated", '
    '"items": [{"name": "item description", "unit": "unit of measure or empty", "qty": 0, "price": 0}], '
    '"notes": ["anything the purchaser should double-check"]}\n'
    "Rules: qty and price are plain numbers (no thousands separators). price is the UNIT price before VAT. "
    "If the document only gives line totals, compute price = total / qty. Keep item names exactly as written "
    "(Arabic stays Arabic). Do not include totals, VAT or discount rows as items. If a field is unknown use an empty string."
)


def _mime(path):
    return {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.webp': 'image/webp'}.get(os.path.splitext(path)[1].lower())


def has_api_key():
    try:
        return bool(os.environ.get('ANTHROPIC_API_KEY') or load_config().get('ai', 'api_key', fallback='').strip())
    except Exception:                                    # noqa
        return False


def read_opts(path):
    """Optional UTF-8 file written by the workbook: lines 'supplier<TAB>name' (names
    already in the suppliers list) and 'exclude<TAB>name' (our own company name)."""
    known, exclude = [], []
    if not path or not os.path.exists(path):
        return known, exclude
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            parts = line.rstrip('\r\n').split('\t', 1)
            if len(parts) == 2 and parts[1].strip():
                if parts[0] == 'supplier':
                    known.append(parts[1].strip())
                elif parts[0] == 'exclude':
                    exclude.append(parts[1].strip())
    return known, exclude


def ai_extract(path, md):
    cp = load_config()
    key = os.environ.get('ANTHROPIC_API_KEY') or cp.get('ai', 'api_key', fallback='').strip()
    if not key:
        raise RuntimeError('لم يتم العثور على مفتاح ANTHROPIC_API_KEY (متغير بيئة أو ملف config.ini).')
    model = cp.get('ai', 'model', fallback='claude-sonnet-4-5').strip() or 'claude-sonnet-4-5'
    url = os.environ.get('QUOTE2PO_API_URL') or cp.get('ai', 'api_url', fallback='https://api.anthropic.com/v1/messages').strip()
    content = []
    mime = _mime(path)
    if mime == 'application/pdf':
        data = base64.b64encode(open(path, 'rb').read()).decode()
        content.append({'type': 'document', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
    elif mime:
        data = base64.b64encode(open(path, 'rb').read()).decode()
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
    if md:
        content.append({'type': 'text', 'text': 'Markdown conversion of the document (may contain extraction errors for Arabic):\n\n' + md[:60000]})
    content.append({'type': 'text', 'text': 'Extract the quotation data as JSON.'})
    body = {'model': model, 'max_tokens': 4096, 'system': AI_PROMPT, 'messages': [{'role': 'user', 'content': content}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST',
                                 headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError('خطأ من خدمة الذكاء الاصطناعي (%s): %s' % (e.code, e.read().decode('utf-8', 'replace')[:300]))
    text = ''.join(b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise RuntimeError('تعذّر فهم ردّ الذكاء الاصطناعي.')
    data = json.loads(m.group(0))
    res = {'supplier': str(data.get('supplier', '') or ''), 'quote_no': str(data.get('quote_no', '') or ''),
           'quote_date': str(data.get('quote_date', '') or ''), 'items': [], 'notes': []}
    for it in data.get('items', []) or []:
        try:
            res['items'].append({'name': str(it.get('name', '')).strip(), 'unit': str(it.get('unit', '') or '').strip(),
                                 'qty': float(it.get('qty') or 0), 'price': float(it.get('price') or 0)})
        except (TypeError, ValueError):
            continue
    res['notes'] += [str(n) for n in (data.get('notes') or [])]
    res['notes'].append('استُخرجت البيانات بالذكاء الاصطناعي — راجعها قبل الاعتماد.')
    return res


# ----------------------------------------------------------------------------- output
def clean_field(s):
    return re.sub(r'[\t\r\n]+', ' ', str(s)).strip()


def write_result(out_path, res=None, error=None, method='', confidence='', xlsx_path=''):
    lines = []
    lines.append('version\t' + VERSION)
    if error and not (res and res['items']):
        lines.append('status\terror')
        lines.append('error\t' + clean_field(error))
    else:
        lines.append('status\tok')
    if method:
        lines.append('method\t' + clean_field(method))
    if confidence:
        lines.append('confidence\t' + clean_field(confidence))
    if xlsx_path:
        lines.append('xlsx\t' + clean_field(xlsx_path))
    if res:
        lines.append('supplier\t' + clean_field(res['supplier']))
        lines.append('quote_no\t' + clean_field(res['quote_no']))
        lines.append('quote_date\t' + clean_field(res['quote_date']))
        for n in res['notes']:
            lines.append('note\t' + clean_field(n))
        for it in res['items']:
            lines.append('item\t%s\t%s\t%s\t%s' % (clean_field(it['name']), clean_field(it['unit']), fmt_num(it['qty']), fmt_num(it['price'])))
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Quotation -> Markdown -> purchase order data')
    ap.add_argument('--version', action='version', version='quote2po ' + VERSION)
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--ai', action='store_true', help='legacy alias for --ai-mode always')
    ap.add_argument('--ai-mode', choices=['auto', 'always', 'never'], default=None,
                     help='auto: use AI only when the local parse is empty/unreliable; '
                          'always: use AI first; never: local parsing only (default)')
    ap.add_argument('--md', help='where to save the Markdown conversion (default: next to output)')
    ap.add_argument('--opts', help='UTF-8 options file from the workbook (known suppliers / own company name)')
    a = ap.parse_args(argv)
    known, exclude = read_opts(a.opts)
    mode = a.ai_mode or ('always' if a.ai else 'never')

    md_path = a.md or os.path.splitext(a.output)[0] + '.md'
    xlsx_path = os.path.splitext(a.output)[0] + '.xlsx'
    md, engine, err = '', '', None
    needs_ai = False
    try:
        md, engine = convert_to_md(a.input)
        with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(md)
    except MissingLibrary as e:
        err = missing_lib_message(str(e))
    except NeedsAI as e:                                  # noqa
        needs_ai = True
        err = ('الملف عبارة عن صورة أو مستند ممسوح ضوئياً ولا يحتوي نصاً قابلاً للقراءة. '
               'فعّل خيار الذكاء الاصطناعي من ورقة الإعدادات، أو أدخل الأصناف يدوياً.')
    except Exception as e:                                # noqa
        err = 'تعذّر تحويل الملف إلى Markdown: %s' % e

    local_res, local_conf = None, 'none'
    if err is None and md:
        try:
            local_res = parse_quote(md, known, exclude)
            local_conf = local_confidence(local_res)
        except Exception as e:                            # noqa
            err = 'تعذّر تحليل محتوى عرض السعر: %s' % e

    use_ai = (mode == 'always') or (needs_ai and mode != 'never') or \
             (mode == 'auto' and local_conf in ('none', 'low') and has_api_key())

    res, method, confidence = None, '', ''
    if use_ai:
        try:
            res = ai_extract(a.input, md)
            method, confidence = 'ai', 'medium'
            err = None
            if res and not res['supplier'] and local_res:
                res['supplier'] = local_res.get('supplier', '')
        except Exception as e:                            # noqa
            if local_res and local_res['items']:
                res = local_res
                method, confidence = 'local', local_conf
                res['notes'].append('تعذّر الاستخراج بالذكاء الاصطناعي (%s) — استُخدمت القراءة المحلية.' % e)
                err = None
            elif not needs_ai:
                err = str(e)
    elif local_res and local_res['items']:
        res, method, confidence = local_res, 'local', local_conf

    if md and (not (res and res['items']) or confidence == 'low'):
        try:
            if md_table_to_xlsx(md, xlsx_path):
                if err is None and not (res and res['items']):
                    err = ('تعذّر استخراج الأصناف تلقائياً بثقة كافية. '
                           'تم تحويل عرض السعر إلى ملف Excel لنسخ البيانات منه يدوياً.')
            else:
                xlsx_path = ''
        except Exception:                                 # noqa
            xlsx_path = ''
    else:
        xlsx_path = ''

    write_result(a.output, res, err, method, confidence, xlsx_path)
    return 0 if (res and res['items']) else 1


if __name__ == '__main__':
    sys.exit(main())
