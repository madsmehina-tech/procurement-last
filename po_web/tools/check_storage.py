# -*- coding: utf-8 -*-
r"""
python tools\check_storage.py  - measures the archive folder exactly the way the system uses it.

Run it ON THE SERVER (to test the disk) and, with the share path as an argument, from a TEAM PC
(to test reading the share):      python tools\check_storage.py \\SERVER\PO_Archive

It checks: the folder exists and is writable, Arabic folder names work, very long paths work, and how
long writing / reading small and large files takes. Nothing is left behind.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def ms(t):
    return "%.0f ms" % (t * 1000)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else config.ARCHIVE_ROOT
    print("المجلد المفحوص:", root)
    problems = []
    if not os.path.isdir(root):
        if len(sys.argv) > 1:
            print("[!!] المجلد غير موجود أو غير قابل للوصول من هذا الجهاز")
            return 1
        os.makedirs(root, exist_ok=True)
        print("[OK] تم إنشاء المجلد")
    probe = os.path.join(root, "_check_storage")
    try:
        os.makedirs(probe, exist_ok=True)
    except OSError as e:
        print("[!!] لا يمكن الكتابة داخل المجلد:", e)
        return 1
    writable = True
    arabic = os.path.join(probe, "الموردين", "شركة الاختبار للتجارة", "مشروع النرجس", "PO-2026-001")
    try:
        os.makedirs(arabic, exist_ok=True)
        open(os.path.join(arabic, "عرض السعر - اختبار.pdf"), "wb").write(b"%PDF test")
        print("[OK] أسماء المجلدات والملفات العربية تعمل")
    except OSError as e:
        problems.append("الأسماء العربية: %s" % e)
    deep = os.path.join(probe, *["مجلد_طويل_للاختبار_%d" % i for i in range(6)])
    try:
        os.makedirs(deep, exist_ok=True)
        open(os.path.join(deep, "ملف.txt"), "w").write("x")
        print("[OK] المسارات الطويلة (%d حرفًا) تعمل" % len(os.path.join(deep, "ملف.txt")))
    except OSError as e:
        problems.append("المسارات الطويلة: %s" % e)

    small = []
    t0 = time.perf_counter()
    for i in range(20):
        t = time.perf_counter()
        with open(os.path.join(probe, "s%d.bin" % i), "wb") as f:
            f.write(os.urandom(50 * 1024))
        small.append(time.perf_counter() - t)
    total_small = time.perf_counter() - t0
    big = os.path.join(probe, "big.bin")
    data = os.urandom(5 * 1024 * 1024)
    t = time.perf_counter()
    with open(big, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    w_big = time.perf_counter() - t
    t = time.perf_counter()
    with open(big, "rb") as f:
        f.read()
    r_big = time.perf_counter() - t
    avg = sum(small) / len(small)
    print("\nالكتابة: ملف صغير (50KB) متوسط %s | 20 ملفًا خلال %s" % (ms(avg), ms(total_small)))
    print("ملف 5MB: كتابة %s (%.1f MB/s) | قراءة %s (%.1f MB/s)" % (ms(w_big), 5 / w_big, ms(r_big), 5 / r_big))

    verdict = "ممتاز"
    if avg > 0.25 or w_big > 4:
        verdict = "بطيء - راجع الشبكة/القرص"
    elif avg > 0.08 or w_big > 1.5:
        verdict = "مقبول"
    print("التقييم:", verdict)

    import shutil
    shutil.rmtree(probe, ignore_errors=True)
    for p in problems:
        print("[!!]", p)
    print("\n" + ("انتهى مع ملاحظات - انسخ الشاشة وأرسلها." if problems else "انتهى بنجاح، ولم يُترك أي ملف."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
