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


def amount_to_words(amount):
    """Full printed line for a SAR amount, e.g. 6999.98 ->
    'فقط ستة آلاف وتسع مائة وتسعة وتسعون ريالاً وثمانية وتسعون هللة لا غير'."""
    amount = round(float(amount or 0), 2)
    if not (0 <= amount < 1_000_000_000):
        raise ValueError("المبلغ خارج النطاق الذي يمكن كتابته بالحروف")
    riyals = int(amount)
    halalas = int(round((amount - riyals) * 100))
    parts = []
    if riyals or not halalas:                          # «صفر ريال» only when there is nothing else to say
        parts.append(_noun_phrase(riyals, 'ريال واحد', 'ريالان', 'ريالات', 'ريالاً') if riyals else 'صفر ريال')
    if halalas:
        parts.append(_noun_phrase(halalas, 'هللة واحدة', 'هللتان', 'هللات', 'هللة'))
    return 'فقط ' + ' و'.join(parts) + ' لا غير'
