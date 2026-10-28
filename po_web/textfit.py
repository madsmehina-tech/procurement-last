# -*- coding: utf-8 -*-
"""
textfit.py - text that the PC's ANSI code page cannot hold.

On a PC whose Windows code page is 1256 (ordinary Arabic Windows) the Access driver can only exchange characters that
exist in that code page. Everything normal is there (all Arabic letters, tashkeel, tatweel, Persian letters, Latin
digits); these are not, and are converted the same way everywhere (database AND archive file names):
  - Arabic-Indic digits  \u0660-\u0669 and Persian digits  \u06F0-\u06F9   -> 0-9
  - the Arabic percent sign and the riyal symbols                     -> % / the word ريال
  - invisible direction marks that PDFs and Word leave in copied text -> removed
Anything else outside the code page becomes '?'. On a PC with the UTF-8 option nothing is touched.
"""
_FIT_MAP = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_FIT_MAP.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_FIT_MAP.update({0x066A: "%", 0xFDFC: "ريال", 0x20C1: "ريال"})
for _cp in (0x200B, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF):
    _FIT_MAP[_cp] = None


def fit_text(value, codec="utf-8"):
    if codec == "utf-8" or not isinstance(value, str):
        return value
    return value.translate(_FIT_MAP).encode(codec, errors="replace").decode(codec)


def fit_wide(value):
    """For a PC whose driver takes UTF-16: only the normalisation (Arabic-Indic digits, invisible marks) - no character is replaced by «?»."""
    if not isinstance(value, str):
        return value
    return value.translate(_FIT_MAP)
