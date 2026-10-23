# -*- coding: utf-8 -*-
"""
textnorm.py - one way to compare Arabic/English names everywhere (projects, items, supplier matching, search).

Two spellings of the same word must give the same key:  إسمنت = اسمنت = أسمنت,  مجمع النرجس = «مجمع  النرجس »,
مدرسه = مدرسة, Cement = cement.  Used for MATCHING only - what the user typed is never changed by this module.
"""
import difflib
import re
import unicodedata

_DIACRITICS = re.compile("[\u064B-\u065F\u0670\u0640\u06D6-\u06ED]")          # tashkeel, dagger alef, tatweel
_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "ک": "ك", "گ": "ك", "ی": "ي",
})
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_NOISE = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")


def has_diacritics(text):
    return bool(_DIACRITICS.search(str(text or "")))


def clean_spaces(text):
    """What a user typed, tidied: no leading/trailing/double spaces (letters untouched)."""
    return _SPACES.sub(" ", str(text or "")).strip()


def norm_key(text):
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = _DIACRITICS.sub("", t).translate(_LETTERS).translate(_DIGITS).lower()
    return _SPACES.sub(" ", _NOISE.sub(" ", t)).strip()


def similarity(a, b):
    ka, kb = norm_key(a), norm_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def best_match(name, candidates, threshold=0.84):
    """(candidate, score) of the most similar candidate at or above the threshold, else (None, best score)."""
    best, score = None, 0.0
    for c in candidates:
        s = similarity(name, c)
        if s > score:
            best, score = c, s
    return (best, score) if score >= threshold else (None, score)


def contains(haystack, needle):
    k = norm_key(needle)
    return not k or k in norm_key(haystack)
