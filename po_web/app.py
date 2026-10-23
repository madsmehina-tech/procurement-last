# -*- coding: utf-8 -*-
"""
app.py - builds the Flask application. All settings come from config.py; all routes live in routes/*.

Development:   python app.py          (debug OFF - never expose the Werkzeug debugger on the network)
Production:    python serve.py        (Waitress - see deploy/ for start-up scripts)
"""
import logging
import threading
import os
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, g, render_template, request, session

import audit
import auth
import config
import db


def _setup_logging(app):
    os.makedirs(config.LOG_DIR, exist_ok=True)

    def make(name, filename, level):
        lg = logging.getLogger(name)
        if not lg.handlers:
            h = RotatingFileHandler(os.path.join(config.LOG_DIR, filename), maxBytes=5_000_000,
                                    backupCount=5, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            lg.addHandler(h)
        lg.setLevel(level)
        lg.propagate = False
        return lg

    app.access_log = make("po.access", "access.log", logging.INFO)
    app.error_log = make("po.error", "error.log", logging.WARNING)


def create_app():
    app = Flask(__name__)
    app.secret_key = config.secret_key()
    app.config.update(
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",          # a form posted from another site never carries the login cookie
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.SESSION_HOURS),
    )
    _setup_logging(app)

    @app.context_processor
    def inject_user():
        user = auth.current_user()
        pending = 0
        if user and auth.can("settings.users"):
            pending = len(db.query("SELECT [UserID] FROM [Users] WHERE [Status] = ?", ("pending",)))
        import cycle_files
        return {"current_user": user, "can": auth.can, "pending_users": pending,
                "staging": config.ENVIRONMENT == "staging", "company_logo": cycle_files.logo()}

    # ONE WRITER AT A TIME. The database is Access (it cannot lock rows) and numbers (purchase orders, cycles, payments, contracts) are
    # "read the last number, add one, insert": two saves at the same instant could take the same number. The server is a single process,
    # so a plain lock around every changing request (POST / PUT / PATCH / DELETE) removes the whole class of problems. Reading pages are not
    # blocked. The same lock is held while the automatic backup copies the database file, so a copy never catches a half-written file.
    write_lock = threading.RLock()
    app.write_lock = write_lock

    @app.before_request
    def _serialize_writes():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            write_lock.acquire()
            g._write_locked = True

    @app.teardown_request
    def _close_read_connection(exc=None):
        db.close_request_conn()

    @app.teardown_request
    def _release_writes(exc=None):
        if g.pop("_write_locked", False):
            write_lock.release()

    @app.before_request
    def _start_timer():
        g._t0 = time.perf_counter()

    @app.after_request
    def _after(resp):
        ms = (time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000
        if not request.path.startswith("/static/"):
            user = session.get("user_id", "-")
            line = "%s %s %s %.0fms user=%s ip=%s" % (request.method, request.path, resp.status_code, ms, user,
                                                      request.remote_addr)
            app.access_log.info(line + (" SLOW" if ms > 1500 else ""))
        audit.log_request(resp)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    def _error_page(code, title, text, detail=None):
        # standalone page: must not touch the database (the database may be the thing that is down)
        return render_template("error.html", code=code, title=title, text=text, detail=detail), code

    @app.errorhandler(404)
    def _404(e):
        return _error_page(404, "الصفحة غير موجودة", "الرابط غير صحيح أو أن الملف لم يعد موجودًا.")

    @app.errorhandler(413)
    def _413(e):
        return _error_page(413, "الملف كبير جدًا", "الحد الأقصى لحجم الملف %d ميجابايت." % config.MAX_UPLOAD_MB)

    @app.errorhandler(500)
    def _500(e):
        orig = getattr(e, "original_exception", e)
        app.error_log.error("500 on %s %s", request.method, request.path, exc_info=orig)
        detail = None
        try:                                    # the real reason is shown to the administrator only (never to other users)
            user = auth.current_user()
            if user and user.get("IsAdmin"):
                detail = "%s: %s" % (type(orig).__name__, " ".join(str(orig).split())[:500])
        except Exception:                       # noqa - the error page must never fail itself
            detail = None
        return _error_page(500, "حدث خطأ غير متوقع", "سُجّل الخطأ في ملف logs/error.log. أخبر مسؤول النظام بوقت حدوثه.", detail)

    import routes
    routes.register_all(app)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False)
