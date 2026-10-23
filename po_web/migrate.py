# -*- coding: utf-8 -*-
"""python migrate.py  -> brings the database up to date.   python migrate.py --status -> shows progress."""
import sys
from migrations import runner

if __name__ == "__main__":
    if "--status" in sys.argv:
        runner.status()
    else:
        sys.exit(0 if runner.run(backup="--no-backup" not in sys.argv) else 1)
