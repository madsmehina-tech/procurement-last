# -*- coding: utf-8 -*-
"""System routes: /health - used by the field test (response times, database and shared-folder status)."""
import time
from datetime import datetime

from flask import jsonify

import archive
import backup
import config
import db
from routes import route


@route("/health")
def health():
    """Open (no login) and deliberately minimal: status + timings only, no paths or data."""
    t0 = time.perf_counter()
    try:
        db.scalar("SELECT COUNT(*) FROM [Users]", default=0)
        db_state = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:                       # noqa - any failure means "database not reachable"
        db_state = {"ok": False, "error": type(e).__name__}
    storage = archive.storage_probe()
    storage.pop("error", None)
    ok = db_state["ok"] and storage["ok"]
    bk = backup.status()
    state = "degraded" if not ok else ("warning" if bk["warnings"] else "ok")      # «warning»: working, but the safety net needs attention (HTTP 200)
    return jsonify(status=state, version=config.APP_VERSION, environment=config.ENVIRONMENT, database=db_state,
                   storage=storage, backup=bk, time=datetime.now().isoformat(timespec="seconds")), (200 if ok else 503)
