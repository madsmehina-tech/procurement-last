# -*- coding: utf-8 -*-
"""
serve.py - production entry point:   python serve.py

Uses Waitress (a real multi-threaded server that works on Windows) instead of Flask's development server.
Settings (host / port / threads) come from config.ini. If Waitress is not installed it says so and falls
back to Flask's server with the debugger OFF - still safe for the network, just slower.
"""
import socket
import sys

import config


def lan_addresses():
    """Addresses the other PCs can use (and the Tailscale one, 100.x.x.x, for the home connection)."""
    found = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def banner():
    print("=" * 66)
    print("  PO Web  v%s   [%s]" % (config.APP_VERSION, config.ENVIRONMENT))
    print("  قاعدة البيانات :", config.DB_PATH)
    print("  مجلد الأرشيف   :", config.ARCHIVE_ROOT)
    print("-" * 66)
    print("  من هذا الجهاز   : http://localhost:%d" % config.PORT)
    for ip in lan_addresses():
        kind = "Tailscale (المنزل)" if ip.startswith("100.") else "الشبكة المحلية"
        print("  %-16s: http://%s:%d" % (kind, ip, config.PORT))
    print("=" * 66)
    print("  للإيقاف: اضغط Ctrl+C")


def main():
    for stream in (sys.stdout, sys.stderr):          # Arabic text must not crash when output goes to a log file
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from migrations import runner
        waiting = runner.pending()
    except Exception as e:                                   # noqa - report it plainly instead of a traceback
        print("!! تعذّر فتح قاعدة البيانات:", e)
        print("   تحقق من مسار القاعدة في config.ini ثم شغّل:  python tools\\setup_check.py")
        return 1
    if waiting:
        print("!! توجد تحديثات للقاعدة لم تُنفَّذ بعد (%d) - لن يعمل النظام قبلها." % len(waiting))
        print("   نفّذ الأمر التالي مرة واحدة ثم أعد التشغيل:   python migrate.py")
        return 1
    from app import app
    banner()
    try:                                                      # automatic copies of the database (a second place: [storage] backup_extra_dir)
        import backup
        backup.start_auto(app.write_lock)
        st = backup.status()
        if config.BACKUP_AUTO_HOURS > 0:
            print("  النسخ الاحتياطي التلقائي: كل %d ساعة%s" % (config.BACKUP_AUTO_HOURS, " + نسخة ثانية في مكان آخر" if st["second_place"] else " (انتبه: لا يوجد مكان ثانٍ للنسخ: backup_extra_dir في config.ini)"))
    except Exception as e:                                    # noqa
        print("!! تعذّر تشغيل النسخ الاحتياطي التلقائي:", e)
    try:
        from waitress import serve
    except ImportError:
        print("!! حزمة waitress غير مثبّتة (pip install waitress) - سيعمل الخادم البديل الأبطأ.")
        app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
        return
    serve(app, host=config.HOST, port=config.PORT, threads=config.THREADS, ident="po-web")


if __name__ == "__main__":
    sys.exit(main())
