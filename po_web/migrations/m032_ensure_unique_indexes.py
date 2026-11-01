# -*- coding: utf-8 -*-
"""
m032_ensure_unique_indexes - completes the unique indexes of m026 on databases where m026 was recorded as applied although an index was skipped
(duplicates were found at that time). It looks again, creates what is missing, and reports honestly: it is NOT recorded as applied while an index
is still missing, so `python migrate.py` keeps telling you until the duplicates are fixed.
"""
from migrations import m026_unique_numbers as m26

DESCRIPTION = "استكمال الفهارس الفريدة التي تخطاها الترحيل m026 بسبب تكرار قديم"


def main():
    return m26.main()
