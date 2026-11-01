# -*- coding: utf-8 -*-
r"""python tools\archive_scan.py "D:\PO_Archive"  [--json out.json]
Read-only measurement of an archive folder: size, files, types, growth per month, projection for 1/3/5 years, long paths, biggest files.
It never writes inside the folder it scans."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage_tools as st


def main():
    if len(sys.argv) < 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        return 2
    s = st.scan_archive(sys.argv[1])
    print(st.scan_report_text(s))
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        st.guard_outputs([out], [sys.argv[1]])
        json.dump(s, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("حُفظ التقرير:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
