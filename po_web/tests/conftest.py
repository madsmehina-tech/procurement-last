# -*- coding: utf-8 -*-
"""Test set-up: replaces the Access layer (db.py) with fake_db.py BEFORE the application is imported, and points every folder at a
temporary place, so a test can never touch a real database, archive or backup."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_tmp = tempfile.mkdtemp(prefix="po_tests_")
for k, v in (("PO_DB_PATH", os.path.join(_tmp, "t.accdb")), ("PO_ARCHIVE_ROOT", os.path.join(_tmp, "archive")),
             ("PO_BACKUP_DIR", os.path.join(_tmp, "backups")), ("PO_LOG_DIR", os.path.join(_tmp, "logs")),
             ("PO_SECRET_KEY", "test-key-not-a-secret"), ("PO_CSRF", "0"), ("PO_CONFIG", os.path.join(_tmp, "none.ini"))):
    os.environ[k] = v
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fake_db  # noqa: E402
sys.modules["db"] = fake_db


def build_schema():
    """A fresh fake database with every table the application uses, built by the application's own migrations."""
    import builtins
    import getpass
    import importlib
    fake_db.reset()
    real_input, real_gp = builtins.input, getpass.getpass
    builtins.input, getpass.getpass = (lambda *a: "admin"), (lambda *a: "Admin12345")
    try:
        from migrations import runner
        for n in runner.discover():
            importlib.import_module("migrations." + n).main()
    finally:
        builtins.input, getpass.getpass = real_input, real_gp
