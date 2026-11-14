# -*- coding: utf-8 -*-
"""
arabic_numbers.py - converts a riyal amount into its Arabic words line for
printed purchase orders ("فقط ... ريالاً و... هللة لا غير"). The exact word
choices (e.g. "تسع مائة" as two words, "ستة آلاف" with the ة-suffixed form)
were matched against the company's own existing printed PO template,
character for character, rather than guessed from general grammar rules.
"""

_ONES = ['', 'واحد', 'اثنان', 'ثلاثة', 'أربعة', 'خمسة', 'ستة', 'سبعة', 'ثمانية', 'تسعة']
_TENS = ['', 'عشرة', 'عشرون', 'ثلاثون', 'أربعون', 'خمسون', 'ستون', 'سبعون', 'ثمانون', 'تسعون']
_TEENS = ['عشرة', 'أحد عشر', 'اثنا عشر', 'ثلاثة عشر', 'أربعة عشر', 'خمسة عشر',
          'ستة عشر', 'سبعة عشر', 'ثمانية عشر', 'تسعة عشر']
_HUNDREDS = ['', 'مائة', 'مائتان', 'ثلاث مائة', 'أربع مائة', 'خمس مائة',
             'ست مائة', 'سبع مائة', 'ثمان مائة', 'تسع مائة']


def _under_100(n):
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, ones = divmod(n, 10)
    return _TENS[tens] if ones == 0 else _ONES[ones] + ' و' + _TENS[tens]


def _under_1000(n):
    if n < 100:
        return _under_100(n)
    h, rest = divmod(n, 100)
    out = _HUNDREDS[h]
    return out + ' و' + _under_100(rest) if rest else out


def _scale_group(n, singular, dual, plural_few, plural_many):
    if n == 1:
        return singular
    if n == 2:
        return dual
    if 3 <= n <= 10:
        return _under_1000(n) + ' ' + plural_few
    return _under_1000(n) + ' ' + plural_many


def number_to_arabic_words(n):
    """Whole number (no currency) in Arabic words, e.g. 6999 -> 'ستة آلاف
    وتسع مائة وتسعة وتسعون'."""
    n = int(round(n))
    if n == 0:
        return 'صفر'
    parts = []
    millions, rest = divmod(n, 1_000_000)
    thousands, rest = divmod(rest, 1000)
    if millions:
        parts.append(_scale_group(millions, 'مليون', 'مليونان', 'ملايين', 'مليوناً'))
    if thousands:
        parts.append(_scale_group(thousands, 'ألف', 'ألفان', 'آلاف', 'ألفاً'))
    if rest or not parts:
        parts.append(_under_1000(rest))
    return ' و'.join(parts)


def _noun_phrase(n, singular_alone, dual, plural, accusative_singular):
    """The count with its noun (riyal / halala).
        1  -> «ريال واحد»      2 -> «ريالان»      3-10 -> «ثلاثة ريالات»
        11-99 and numbers ending in 11-99 -> the noun in the accusative: «أحد عشر ريالاً»
        a round hundred / thousand / million (100, 1000, 46000, 2000000 ...) -> the bare noun: «مائة ريال»، «ستة وأربعون ألف ريال»
        a round 2 (200, 2000, 2000000)  -> the dual in construct form: «مائتا ريال»، «ألفا ريال»
    The word choices inside the number itself («ثلاث مائة» ...) are the company's own printed style and are not changed."""
    if n == 1:
        return singular_alone
    if n == 2:
        return dual
    words = number_to_arabic_words(n)
    if 3 <= n <= 10:
        return words + ' ' + plural
    bare = accusative_singular.rstrip('اً')
    for tanween in ('ألفاً', 'مليوناً'):                  # «ألفاً ريالاً» is a double tanween: the scale word loses it before the noun
        if words.endswith(tanween):
            return words[:-2] + ' ' + bare
    for dual_form in ('ألفان', 'مليونان', 'مائتان'):       # 2000 / 2,000,000 / 200 -> «ألفا ريال»
        if words.endswith(dual_form):
            return words[:-2] + 'ا ' + bare
    if n >= 100 and n % 100 == 0:
        return words + ' ' + bare
    return words + ' ' + accusative_singular


# code: (name shown in lists, (one, two, 3-10 plural, accusative), (sub one, two, plural, accusative) or None when the currency has no small unit)
CURRENCIES = {
    "SAR": ("ريال سعودي", ("ريال واحد", "ريالان", "ريالات", "ريالاً"), ("هللة واحدة", "هللتان", "هللات", "هللة")),
    "USD": ("دولار أمريكي", ("دولار واحد", "دولاران", "دولارات", "دولاراً"), ("سنت واحد", "سنتان", "سنتات", "سنتاً")),
    "EUR": ("يورو", ("يورو واحد", "اثنان يورو", "يورو", "يورو"), ("سنت واحد", "سنتان", "سنتات", "سنتاً")),
    "GBP": ("جنيه إسترليني", ("جنيه واحد", "جنيهان", "جنيهات", "جنيهاً"), ("بنس واحد", "بنسان", "بنسات", "بنساً")),
    "AED": ("درهم إماراتي", ("درهم واحد", "درهمان", "دراهم", "درهماً"), ("فلس واحد", "فلسان", "فلوس", "فلساً")),
    "JPY": ("ين ياباني", ("ين واحد", "ينان", "ينات", "يناً"), None),
    "CNY": ("يوان صيني", ("يوان واحد", "يوانان", "يوانات", "يواناً"), None),
}
SHORT = {"SAR": "ريال", "USD": "دولار", "EUR": "يورو", "GBP": "جنيه إسترليني", "AED": "درهم", "JPY": "ين", "CNY": "يوان"}


def currency_code(code):
    """A known currency code, SAR when empty or unknown."""
    c = (code or "SAR").strip().upper()
    return c if c in CURRENCIES else "SAR"


def amount_to_words(amount, currency="SAR"):
    """Full printed line for an amount, e.g. 6999.98 ->
    'فقط ستة آلاف وتسع مائة وتسعة وتسعون ريالاً وثمانية وتسعون هللة لا غير'. Other currencies use their own nouns («دولاراً»، «سنتاً»)."""
    _name, main, sub = CURRENCIES[currency_code(currency)]
    amount = round(float(amount or 0), 2 if sub else 0)
    if not (0 <= amount < 1_000_000_000):
        raise ValueError("المبلغ خارج النطاق الذي يمكن كتابته بالحروف")
    whole = int(amount)
    small = int(round((amount - whole) * 100)) if sub else 0
    parts = []
    if whole or not small:                             # «صفر ريال» only when there is nothing else to say
        parts.append(_noun_phrase(whole, *main) if whole else 'صفر ' + main[3].rstrip('اً'))
    if small:
        parts.append(_noun_phrase(small, *sub))
    return 'فقط ' + ' و'.join(parts) + ' لا غير'
