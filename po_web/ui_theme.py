# -*- coding: utf-8 -*-
"""«المظهر» - the look of the whole system, chosen by the system administrator and applied to everyone.

Five ready-made templates (colour sets that were checked to stay readable) + the font, the text size, the table density.
Stored as JSON in CompanyProfile.ThemeJson; empty = the look of version 28 exactly (the dark-green template, Cairo, medium, comfortable).
The page gets ONE small <style> block (css()) that only redefines the design tokens of style.css, so every page follows without being touched.
"""
import json
import logging
import re

import db

log = logging.getLogger(__name__)

# accent = buttons/links, accent_hover, accent_bg = light tint behind selected items, side = sidebar, gold = second colour, bg = page
PRESETS = {
    "green": {"name": "الأخضر الداكن (الحالي)", "accent": "#29595E", "side": "#1E293B", "gold": "#C08D34", "bg": "#F4F6F8"},
    "blue": {"name": "أزرق مؤسسي", "accent": "#1D4E89", "side": "#0F1E33", "gold": "#C9822B", "bg": "#F3F6FA"},
    "grey": {"name": "رمادي رسمي", "accent": "#3F4A56", "side": "#1F242B", "gold": "#9C7A3C", "bg": "#F4F4F5"},
    "earth": {"name": "ترابي (مقاولات)", "accent": "#7B4F24", "side": "#2A2018", "gold": "#B8901F", "bg": "#F6F3EE"},
    "brand": {"name": "ألوان الشعار", "accent": None, "side": None, "gold": "#B8901F", "bg": "#F5F5F4"},
}
FONTS = {
    "cairo": ("Cairo", '"Cairo"'),
    "tajawal": ("Tajawal", '"Tajawal"'),
    "plex": ("IBM Plex Arabic", '"IBM Plex Sans Arabic"'),
}
SIZES = {"small": ("صغير", 0.92), "medium": ("متوسط", 1.0), "large": ("كبير", 1.1)}
DENSITY = {"comfortable": ("مريحة", "15px"), "compact": ("مضغوطة", "8px")}
DEFAULT = {"preset": "green", "brand": "", "font": "cairo", "size": "medium", "density": "comfortable"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ------------------------------------------------------------------ colours
def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _hex(c):
    return "#" + "".join("%02X" % max(0, min(255, round(v))) for v in c)


def mix(a, b, t):
    x, y = _rgb(a), _rgb(b)
    return _hex([v + (y[i] - v) * t for i, v in enumerate(x)])


def _lum(h):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readable(color):
    """(colour, darkened?) - a brand colour too light for white text on a button is darkened until it reaches 4.5:1."""
    c, n = color, 0
    while contrast(c, "#FFFFFF") < 4.5 and n < 25:
        c, n = mix(c, "#000000", 0.08), n + 1
    return c, n > 0


# ------------------------------------------------------------------ settings
def clean(raw):
    """Only known values are kept: anything else falls back to the default."""
    raw = raw if isinstance(raw, dict) else {}
    s = dict(DEFAULT)
    if raw.get("preset") in PRESETS:
        s["preset"] = raw["preset"]
    if HEX.match(str(raw.get("brand") or "")):
        s["brand"] = raw["brand"].upper()
    for key, table in (("font", FONTS), ("size", SIZES), ("density", DENSITY)):
        if raw.get(key) in table:
            s[key] = raw[key]
    if s["preset"] == "brand" and not s["brand"]:
        s["preset"] = "green"                                   # «ألوان الشعار» without a colour = the default
    return s


_cache = {"s": None}


def get():
    if _cache["s"] is not None:
        return dict(_cache["s"])
    s = dict(DEFAULT)
    try:
        rows = db.query("SELECT [ThemeJson] FROM [CompanyProfile] WHERE [ProfileID] = 1")
        if rows and rows[0].get("ThemeJson"):
            s = clean(json.loads(rows[0]["ThemeJson"]))
    except Exception:                                           # noqa - an older database (before m035) or a broken value: the default look
        log.debug("theme settings not readable", exc_info=True)
    _cache["s"] = s
    return dict(s)


def save(raw):
    s = clean(raw)
    text = json.dumps(s, ensure_ascii=False)
    if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
        db.execute("UPDATE [CompanyProfile] SET [ThemeJson] = ? WHERE [ProfileID] = 1", (text,))
    else:
        db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [ThemeJson]) VALUES (1, ?)", (text,))
    _cache["s"] = s
    return s


def reset_cache():
    _cache["s"] = None


# ------------------------------------------------------------------ what the page gets
def tokens(s=None):
    """The design tokens of style.css for these settings."""
    s = clean(s if s is not None else get())
    p = PRESETS[s["preset"]]
    if s["preset"] == "brand":
        accent, _ = readable(s["brand"])
        side = mix(accent, "#000000", 0.72)
    else:
        accent, side = p["accent"], p["side"]
    return {
        "--accent": accent,
        "--accent-hover": mix(accent, "#000000", 0.18),
        "--accent-bg": mix(accent, "#FFFFFF", 0.88),
        "--sage": mix(accent, "#FFFFFF", 0.55),
        "--gold": p["gold"],
        "--bg": p["bg"],
        "--sidebar-bg": side,
        "--sidebar-border": mix(side, "#FFFFFF", 0.12),
        "--accent-deep": mix(accent, "#000000", 0.35),
        "--accent-link": accent,
        "--ui-font": FONTS[s["font"]][1],
        "--ui-zoom": str(SIZES[s["size"]][1]),
        "--row-pad": DENSITY[s["density"]][1],
    }


def css(s=None):
    """The <style> block every page carries (empty for the default look, so version 28 pages are byte-for-byte what they were)."""
    s = clean(s if s is not None else get())
    if s == DEFAULT:
        return ""
    body = "; ".join("%s: %s" % kv for kv in tokens(s).items())
    return ":root { %s; }" % body


def font_css_needed(s=None):
    """Tajawal / IBM Plex are loaded only when chosen."""
    s = clean(s if s is not None else get())
    return s["font"] != "cairo"


def describe(s=None):
    s = clean(s if s is not None else get())
    return "%s · %s · %s · %s" % (PRESETS[s["preset"]]["name"], FONTS[s["font"]][0], SIZES[s["size"]][0], DENSITY[s["density"]][0])
