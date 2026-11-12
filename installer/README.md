# مثبّت نظام المشتريات (PO Setup)

يثبّت النظام على **الجهاز الرئيسي** (الذي فيه القاعدة ومجلد الشير) بنقرات قليلة، ويقوم بدلاً منك بما كان في الدليل اليدوي (المرحلتان 3 و5).

## الاستعمال (الطريقة الأسهل)
1. حمّل **`PO_Setup.exe`** من GitHub: التبويب **Actions** ← آخر تشغيل ناجح لـ **installer** ← **Artifacts** ← `PO_Setup` (ملف واحد فيه البرنامج نفسه).
2. انقر عليه نقراً مزدوجاً ووافق على طلب «مسؤول النظام» (UAC). قد تُظهر ويندوز تحذير «SmartScreen» لأن الملف غير موقّع تجارياً: اختر «مزيد من المعلومات» ثم «تشغيل على أي حال».
3. في النافذة:
   - **١) مجلد الشير:** اختر أولاً مجلد الأرشيف المشترك (مثل `D:\PO_Archive`). اضغط «اقترح باقي المسارات» فتُقترح بقية المجلدات على القرص نفسه.
   - **٢) القاعدة والنسخ:** قاعدة جديدة أو نسخة من قاعدة موجودة، ومجلدا النسخ الاحتياطي (قرص آخر يُفضَّل).
   - **٣) حساب المدير** (للقاعدة الجديدة).
   - **٤) خيارات:** Tesseract، الجدار الناري، التشغيل التلقائي، التشغيل بعد التثبيت.
   - **٥) التثبيت:** اضغط «ابدأ التثبيت» وانتظر. تظهر كل خطوة بعلامة ✔ أو ⚠ أو ✘.

## ما الذي يفعله بالترتيب
فحص الجهاز ← **بايثون 3.12** (مع Add to PATH، فقط إن لم يوجد مناسب) ← **سائق Access** (ACE x64) ← **Tesseract مع العربية** (اختياري) ← إنشاء المجلدات ← **مشاركة مجلد الشير** (قراءة فقط للفريق) ← **فك البرنامج** في `<مجلد التثبيت>\app` ← قاعدة Access (جديدة فارغة أو نسخة من قاعدتك) ← **كتابة config.ini** ← `pip install -r requirements.txt` ← `migrate.py` ← `setup_check.py` و`check_storage.py` ← الجدار الناري ← التشغيل التلقائي ← تشغيل الخادم وفحص `/health`.

## ضمانات
- **لا يحذف شيئاً**، ولا يكتب فوق قاعدة أو أرشيف موجودين. «قاعدة موجودة» تُنسخ ولا يُلمس الأصل.
- مثبّتات بايثون وAccess **تُنزَّل من مواقعها الرسمية ويُتحقق من توقيعها الرقمي** (Python Software Foundation / Microsoft) قبل التشغيل، وTesseract من GitHub مع بصمة SHA-256 مثبَّتة. حزمة البرنامج تُفحص ببصمتها.
- كلمة مرور المدير تُمرَّر للتهيئة فقط ولا تُحفظ ولا تُكتب في السجل. السجل: `<مجلد التثبيت>\install_log.txt`.
- إذا فشلت خطوة مهمة يتوقف التثبيت ويشرح السبب. وبعد إصلاح السبب أعد تشغيل الأداة: الخطوات قابلة للتكرار.
- **ترقية:** فعّل «استبدل برنامجاً قديماً»: يُنقل القديم إلى `app_old_…` ويُحتفظ بـ `config.ini` و`instance`.

## وضع بلا نافذة (لمن يريد تكراره)
```
PO_Setup.exe --dry-run --answers answers.json      لعرض الخطة فقط
PO_Setup.exe --silent  --answers answers.json      لتنفيذ كامل
```
ومثال `answers.json`: `{"share_folder": "D:\\PO_Archive", "base": "D:\\PO", "admin_user": "admin", "admin_password": "...", "db_mode": "new"}`.

## بدائل
- بلا EXE: ضع `Install-PO.bat` و`po_setup.py` وحزمة `po_web_v*.zip` في مجلد واحد وانقر على `Install-PO.bat`.
- **ما لا يفعله:** نقل بياناتك الحقيقية من النظام القديم (يُفعل يدوياً بعد التجربة، انظر دليل التجربة)، ولا يغيّر شيئاً في النظام القديم.

## ما الذي اختُبر فعلاً (4 نوفمبر)
- **على جهاز ويندوز حقيقي** (في GitHub Actions): تشغيل المثبّت كاملاً بلا تدخل: بايثون، تنزيل سائق Access وتثبيته بصمت، إنشاء قاعدة Access جديدة، فك البرنامج، `config.ini`، `pip install`، **33 ترحيلاً على محرك Access الحقيقي**، فحص الجاهزية، تشغيل الخادم، و`/health` يردّ.
- ثم **اختبار القبول على نسخة منها**: التراجع عند فشل المعاملة ✔، النص العربي ✔، الصفحات (الأبطأ 95 مللي ثانية) ✔، النسخ والاسترجاع ببصمة كل الملفات ✔، والكتابة المتزامنة ⚠ (كل السجلات كُتبت، وبعض الكتابات احتاجت إعادة محاولة بسبب قفل Access عند 4 خيوط + عمليتين معاً).
- أخطاء وجدها هذا الاختبار وأُصلحت: طباعة العربية عند تحويل المخرجات، وحزمة PowerShell، و`migrate.py` على قاعدة فارغة، ونص رسالة Access للفهرس الموجود.
- **لم يُختبر هنا:** نافذة الويزارد بالفأرة (فُحص منطقها فقط)، ومشاركة الشير على شبكة مكتب حقيقية، وتثبيت بايثون من الصفر (الجهاز الاختباري فيه بايثون)، وTesseract على جهازكم، والجهاز الفعلي للشركة.

## One file for the main PC AND the team PCs (v1.1)

`PO_Setup.exe` now opens with a first screen: **main PC** (full installation, as before) / **team PC** / **remove the network settings**.

| | What it does |
|---|---|
| Main PC | as before, plus: makes a *Public* network *Private* (a Public network blocked the team: the firewall rule is for private networks), firewall rule for the office subnet (+ optional extra ranges, never the whole internet), friendly name (`poweb`, or a dotted name such as `alhadab.procurement`) as a tagged line in `hosts`, and writes `client_import.json` (no passwords) |
| Team PC | reads `client_import.json` (or an address typed by hand), **tests the connection** (port, then `/health`) and says which layer fails, adds the friendly-name line (admin only), puts a shortcut on the Desktop, opens the system. Outside the office it points the name to the Tailscale address |
| Remove | deletes only what the tool added (firewall rules by name, tagged `hosts` lines, shortcuts, restores the network type it changed). **Never touches the program, the database or the archive** |

Command line: `PO_Setup.exe --role client --silent --import-file client_import.json`, `PO_Setup.exe --remove-lan`.
What was really tested: logic (33 tests); on real Windows in CI: the host install, the exported file, the `hosts` line, the firewall rules, the client run (connection test, shortcut) and the removal. The windows are opened and closed by a self-test; clicking by a person is not covered.

## Full backup / export and restore on a new main PC (v1.2)

First screen → **«نسخ احتياطي كامل وتصدير»**: choose the program folder (`app`) and a destination; it writes one `PO_Export_<date>.zip` with the database, every archive file,
the old uploads, `instance` and a manifest (size + SHA-256 per file), then re-reads the package and checks every file. It only reads the installation and refuses while the database is open.
On the new PC: **الجهاز الرئيسي → «استرداد من حزمة تصدير»**. The database goes to `<base>\data`, the archive to the share folder chosen there (paths in the database are relative, so the folder may differ),
each file is checked against the manifest, nothing existing is ever overwritten, then the normal steps run (migrate, checks, firewall, start).
Command line: `PO_Setup.exe --export --app D:\PO\app --dest E:\Backup`, `PO_Setup.exe --verify-package E:\Backup\PO_Export_....zip`.
Tested: logic (8 tests) and, on real Windows in CI, an export of a real installation followed by a second installation restored from it (server answers, same archive files).

## Start at boot with nobody signed in, and Windows accounts (v1.3)

Main PC, page «خيارات»: **«شغّل النظام عند إقلاع الجهاز دون دخول أحد»**. Account empty = SYSTEM. With a Windows account + its password the task runs as that account
(«run whether the user is signed in or not»); the installer grants it Modify on the data / archive / backup folders. The password goes to Task Scheduler only (child-process environment), never to a file or the log.
Team PC: optional **Windows account of the main PC** + password, saved with `cmdkey` in that PC's Credential Manager for the main PC's address / name / friendly name, so the shared archive opens without a prompt. «Remove» deletes them.
Tested on real Windows in CI: a non-administrator local account is created, the server is started by the task and its process owner is that account; the client saves and removes the credential; the password is not in the log.
Not covered: Microsoft Word automation without an interactive session (unsupported by Microsoft; LibreOffice is the dependable backend there).
