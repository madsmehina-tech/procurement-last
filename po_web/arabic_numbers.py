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
    """Picks the right noun form for a small standalone count (1 / 2 / 3-10)
    vs. any larger or compound amount (which always takes the standard
    singular-accusative tamyiz form, regardless of its trailing digit -
    "7003" is itself in the 11+/compound category, not a small number, even
    though its last digit is 3)."""
    if n == 1:
        return singular_alone
    if n == 2:
        return dual
    if 3 <= n <= 10:
        return number_to_arabic_words(n) + ' ' + plural
    return number_to_arabic_words(n) + ' ' + accusative_singular


def amount_to_words(amount):
    """Full printed line for a SAR amount, e.g. 6999.98 ->
    'فقط ستة آلاف وتسع مائة وتسعة وتسعون ريالاً وثمانية وتسعون هللة لا غير'."""
    amount = round(float(amount or 0), 2)
    riyals = int(amount)
    halalas = int(round((amount - riyals) * 100))
    words = 'فقط ' + _noun_phrase(riyals, 'ريال واحد', 'ريالان', 'ريالات', 'ريالاً')
    if halalas:
        words += ' و' + _noun_phrase(halalas, 'هللة واحدة', 'هللتان', 'هللات', 'هللة')
    words += ' لا غير'
    return words
