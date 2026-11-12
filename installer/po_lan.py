# -*- coding: utf-8 -*-
"""
PO Setup - the NETWORK part: the main PC (host) and the team PCs (clients), in the same tool.

Host (main PC) - done by PO_Setup together with the installation:
   * the network of the PC is made «Private» (a «Public» network blocks the team: the firewall rule is for private networks only)
   * the firewall rule for the port (office network + optional extra ranges + Tailscale) - never the whole internet
   * a friendly name for the link (e.g. http://poweb:5000) as a tagged line in the hosts file
   * client_import.json: the file a team PC reads to set itself up (no passwords inside)
Client (team PC):
   * reads client_import.json (or the address typed by hand), TESTS the connection (port + /health), tells the person exactly what is wrong if it fails
   * optional friendly-name line in the hosts file, and a desktop shortcut that opens the system
Everything that changes the PC is recorded in lan_state.json so that «remove» undoes exactly that and nothing else.
The program, the database and the archive are NEVER touched by «remove».
"""
import dataclasses
import ipaddress
import json
import os
import re
import socket
import urllib.request
from datetime import datetime

HOSTS_TAG = "# PO-SETUP"
NAME_RULE = re.compile(r"^(?=.{2,60}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")      # one or more labels separated by dots, e.g. poweb or alhadab.procurement
RULE_LAN = "PO Web (LAN)"
RULE_TS = "PO Web (Tailscale)"


def program_data_dir():
    if os.name != "nt":                                   # (tests / other systems: never write into a Windows-looking path)
        import tempfile
        return os.path.join(tempfile.gettempdir(), "PO_Setup")
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "PO_Setup")


def state_path():
    return os.path.join(program_data_dir(), "lan_state.json")


def hosts_path():
    if os.name != "nt":
        return ""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")


# ------------------------------------------------------------------------------------------------------------- small checks
def parse_remote_list(text):
    """«192.168.5.0/24, 10.0.0.0/8» -> (list, errors). Only real addresses / ranges are accepted («LocalSubnet» is always there anyway)."""
    items, errs = [], []
    for part in re.split(r"[,\s;]+", (text or "").strip()):
        if not part or part.lower() == "localsubnet":
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            errs.append("عنوان/نطاق غير صالح: %s" % part)
            continue
        if net.prefixlen == 0:
            errs.append("النطاق %s يفتح الإنترنت كله ولا يُقبل." % part)
            continue
        items.append(str(net))
    return items, errs


def valid_alias(name):
    return bool(NAME_RULE.match((name or "").strip().lower()))


def detect_ip():
    """The address of this PC on the office network (the one used to reach the router)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))                         # no packet is sent: it only asks the OS which address it would use
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and not is_tailscale(ip):
                return ip
    except OSError:
        pass
    return ""


def is_tailscale(ip):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def detect_tailscale_ip():
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if is_tailscale(ip):
                return ip
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------------------------------------------------- the hosts file (tagged lines only)
def hosts_set(path, ip, name, now=None):
    """Adds «ip name  # PO-SETUP» (replacing an older line of ours for the same name). Keeps a one-time backup. Returns True if the file changed."""
    name = name.strip().lower()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    kept = [l for l in lines if not (HOSTS_TAG in l and re.search(r"\s%s(\s|$)" % re.escape(name), " " + l))]
    new = "%s\t%s\t%s %s" % (ip, name, HOSTS_TAG, (now or datetime.now()).strftime("%Y-%m-%d"))
    out = kept + [new]
    if out == lines:
        return False
    bak = path + ".po_setup.bak"
    if os.path.isfile(path) and not os.path.exists(bak):
        with open(path, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(out) + "\r\n")
    return True


def hosts_remove(path):
    """Removes only the lines carrying our tag. Returns how many."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return 0
    kept = [l for l in lines if HOSTS_TAG not in l]
    n = len(lines) - len(kept)
    if n:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join(kept) + "\r\n")
    return n


# ------------------------------------------------------------------------------------------------------------- state (what we changed)
def load_state(path=None):
    try:
        with open(path or state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    p = path or state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------------------------------------------------- HOST steps (called by po_setup)
def host_network_profile(c, ps, ps_quote):
    """Makes the network of the PC «Private» (only if it is «Public»), remembering what was changed."""
    ip = detect_ip()
    if c.dry or not ip:
        return "ok" if c.dry else "warn", "سيُفحص نوع الشبكة ويُحوَّل إلى Private إن كان Public" if c.dry else "تعذّر معرفة عنوان الجهاز؛ حوّل الشبكة إلى Private يدوياً."
    script = ("$i=(Get-NetIPAddress -AddressFamily IPv4 -IPAddress %s -ErrorAction Stop).InterfaceIndex; $p=Get-NetConnectionProfile -InterfaceIndex $i -ErrorAction Stop; "
              "Write-Output ('CAT=' + $p.NetworkCategory + ';IDX=' + $i); "
              "if ($p.NetworkCategory -eq 'Public') { Set-NetConnectionProfile -InterfaceIndex $i -NetworkCategory Private; Write-Output 'CHANGED=1' }") % ps_quote(ip)
    code, out = c.run(ps(script))
    if code != 0:
        return "warn", "تعذّر ضبط نوع الشبكة: شغّل  Set-NetConnectionProfile -NetworkCategory Private  يدوياً."
    m = re.search(r"CAT=(\w+);IDX=(\d+)", out)
    if "CHANGED=1" in out and m:
        st = load_state()
        st["network_profile"] = {"interface_index": int(m.group(2)), "was": m.group(1)}
        save_state(st)
        return "ok", "كانت الشبكة Public فحُوّلت إلى Private (بدونها يحجب الجدار الناري أجهزة الفريق)."
    return "ok", "الشبكة من نوع %s — لا تغيير." % (m.group(1) if m else "؟")


def host_alias_and_export(c):
    """The friendly name on the host itself + client_import.json."""
    a = c.a
    ip = detect_ip()
    ts = detect_tailscale_ip()
    alias = (a.alias_name or "").strip().lower()
    if alias and not valid_alias(alias):
        return "warn", "الاسم البديل «%s» غير صالح (حروف إنجليزية صغيرة وأرقام وشرطة، 2-30)؛ لم يُنشأ." % alias
    notes = []
    st = load_state() if not c.dry else {}
    if alias and ip and not c.dry and hosts_path():
        try:
            hosts_set(hosts_path(), ip, alias)
            st["hosts"] = sorted(set(st.get("hosts", []) + [alias]))
            notes.append("الاسم http://%s:%d يعمل على هذا الجهاز" % (alias, int(a.port)))
        except OSError as e:
            notes.append("تعذّر تعديل ملف hosts (%s)" % e)
    data = {"product": "po_web", "version": 1, "server_name": socket.gethostname(), "server_ip": ip, "port": int(a.port), "alias": alias, "tailscale_ip": ts,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
    out_dir = a.export_dir or a.base
    path = os.path.join(out_dir, "client_import.json")
    if not c.dry:
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            notes.append("ملف الفريق: %s (انسخه لكل جهاز مع PO_Setup.exe)" % path)
        except OSError as e:
            notes.append("تعذّر كتابة client_import.json (%s)" % e)
        save_state(st)
    status = "ok" if ip else "warn"
    if not ip:
        notes.append("لم أستطع معرفة عنوان الجهاز: اكتبه يدوياً في client_import.json")
    notes.append("مهم: ثبّت عنوان هذا الجهاز من الراوتر (DHCP Reservation) وإلا قد يتغير الرابط.")
    return status, "؛ ".join(notes)


# ------------------------------------------------------------------------------------------------------------- CLIENT
@dataclasses.dataclass
class ClientAnswers:
    import_file: str = ""            # client_import.json (optional)
    server: str = ""                 # IP or name typed by hand
    port: int = 5000
    alias: str = ""                  # friendly name -> hosts line
    outside_office: bool = False     # the PC is used outside the office through Tailscale
    tailscale_ip: str = ""
    shortcut: bool = True
    shortcut_name: str = "نظام المشتريات"
    open_browser: bool = True


def load_import(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("product") != "po_web":
        raise ValueError("هذا ليس ملف إعداد الفريق (client_import.json) الصادر من الجهاز الرئيسي.")
    return data


def apply_import(a, data):
    a.server = data.get("server_ip") or data.get("server_name") or a.server
    a.port = int(data.get("port") or a.port)
    a.alias = data.get("alias") or a.alias
    a.tailscale_ip = data.get("tailscale_ip") or a.tailscale_ip
    return a


def client_target(a):
    """(ip/name to reach, hosts alias target) - outside the office the name points to the Tailscale address."""
    reach = a.tailscale_ip if (a.outside_office and a.tailscale_ip) else a.server
    return reach


def check_connection(host, port, timeout=5, opener=None):
    """(ok, message in Arabic). Port first, then /health, so the message says WHICH layer fails."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
    except OSError as e:
        return False, ("لا يصل هذا الجهاز إلى %s:%s (%s). الأسباب المعتادة: الجهاز الرئيسي مغلق أو الخادم لا يعمل؛ شبكة الجهاز الرئيسي Public؛ "
                       "الجهازان على شبكتين مختلفتين؛ أو عنوان الخادم تغيّر." % (host, port, type(e).__name__))
    url = "http://%s:%s/health" % (host, port)
    try:
        with (opener or urllib.request.urlopen)(url, timeout=timeout) as r:
            if getattr(r, "status", 200) == 200:
                return True, "الاتصال سليم: الخادم يردّ على %s" % url
    except Exception as e:                                     # noqa: BLE001
        return False, "المنفذ مفتوح لكن النظام لم يردّ على /health (%s)." % type(e).__name__
    return False, "المنفذ مفتوح لكن النظام لم يردّ على /health."


def run_client(a, log, ps, ps_quote, is_admin, dry=False, desktop=None, hosts=None, tester=None, opener_fn=None):
    """The client steps. Returns (results, overall) like run_install."""
    results, overall = [], "ok"
    admin = bool(is_admin() if callable(is_admin) else is_admin)

    def add(key, title, status, detail):
        nonlocal overall
        results.append((key, title, status, detail))
        log("[%s] %s - %s" % (status.upper(), title, detail))
        if status == "fail":
            overall = "fail"
        elif status == "warn" and overall == "ok":
            overall = "warn"

    if a.import_file:
        try:
            apply_import(a, load_import(a.import_file))
            add("import", "قراءة ملف الفريق", "ok", "%s : %s" % (a.server, a.port))
        except (OSError, ValueError) as e:
            add("import", "قراءة ملف الفريق", "fail", str(e))
            return results, overall
    if not a.server:
        add("import", "عنوان الخادم", "fail", "اكتب عنوان الجهاز الرئيسي (مثل 192.168.100.55) أو اختر ملف client_import.json.")
        return results, overall
    reach = client_target(a)
    ok, msg = (tester or check_connection)(reach, a.port) if not dry else (True, "[dry-run]")
    add("test", "اختبار الاتصال", "ok" if ok else "fail", msg)
    if not ok:
        return results, overall                                 # nothing else is worth doing while the server cannot be reached
    state = load_state() if not dry else {}
    alias = (a.alias or "").strip().lower()
    link = "http://%s:%d" % (alias if alias else reach, int(a.port))
    if alias:
        if not valid_alias(alias):
            add("alias", "الاسم البديل", "warn", "الاسم «%s» غير صالح؛ سيُستعمل العنوان الرقمي." % alias)
            link = "http://%s:%d" % (reach, int(a.port))
        elif dry:
            add("alias", "الاسم البديل", "ok", "سيُربط %s بالعنوان %s" % (alias, reach))
        elif not admin:
            add("alias", "الاسم البديل", "warn", "يلزم تشغيل الأداة كمسؤول لإضافة الاسم؛ سيُستعمل العنوان الرقمي.")
            link = "http://%s:%d" % (reach, int(a.port))
        else:
            try:
                hosts_set(hosts or hosts_path(), reach, alias)
                state["hosts"] = sorted(set(state.get("hosts", []) + [alias]))
                add("alias", "الاسم البديل", "ok", "%s يشير إلى %s (سطر موسوم في ملف hosts؛ تُزال بخيار الإزالة)" % (alias, reach))
            except OSError as e:
                add("alias", "الاسم البديل", "warn", "تعذّر تعديل ملف hosts (%s)؛ سيُستعمل العنوان الرقمي." % e)
                link = "http://%s:%d" % (reach, int(a.port))
    if a.shortcut:
        try:
            lnk = _make_shortcut(a, link, ps, ps_quote, dry, desktop)
            if lnk:
                state.setdefault("shortcuts", [])
                if lnk not in state["shortcuts"]:
                    state["shortcuts"].append(lnk)
            add("shortcut", "اختصار سطح المكتب", "ok", lnk or "سيُنشأ اختصار")
        except OSError as e:
            add("shortcut", "اختصار سطح المكتب", "warn", "تعذّر إنشاء الاختصار (%s). افتح الرابط يدوياً: %s" % (e, link))
    if not dry:
        save_state(state)
    add("link", "الرابط", "ok", link)
    if a.open_browser and not dry:
        try:
            os.startfile(link)                                        # noqa - Windows only
        except (AttributeError, OSError):
            pass
    return results, overall


def _make_shortcut(a, link, ps, ps_quote, dry, desktop=None):
    """An Internet shortcut (.url): no COM, works everywhere. Goes to the Desktop of the person who runs the tool."""
    if dry:
        return ""
    folder = desktop
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Desktop")
        try:
            import subprocess
            out = subprocess.run(ps("[Environment]::GetFolderPath('Desktop')"), capture_output=True, timeout=30).stdout.decode("utf-8", "replace").strip()
            if out and os.path.isdir(out):
                folder = out
        except Exception:                                              # noqa: BLE001
            pass
    safe = re.sub(r'[\\/:*?"<>|]', "", a.shortcut_name).strip() or "PO"
    path = os.path.join(folder, safe + ".url")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("[InternetShortcut]\r\nURL=%s\r\n" % link)
    return path


# ------------------------------------------------------------------------------------------------------------- REMOVE (only what this tool changed)
def remove_lan(c_run, ps, hosts=None, state=None):
    """Undoes exactly what lan_state.json says + the tagged hosts lines + the firewall rules by name. Returns a list of lines."""
    done = []
    st = state if state is not None else load_state()
    for rule in (RULE_LAN, RULE_TS):
        code, _o = c_run(["netsh", "advfirewall", "firewall", "delete", "rule", "name=" + rule])
        done.append("قاعدة الجدار الناري «%s»: %s" % (rule, "حُذفت" if code == 0 else "غير موجودة"))
    n = hosts_remove(hosts or hosts_path()) if (hosts or hosts_path()) else 0
    done.append("أسطر ملف hosts الموسومة: حُذف %d" % n)
    for lnk in st.get("shortcuts", []):
        try:
            os.remove(lnk)
            done.append("اختصار: حُذف " + lnk)
        except OSError:
            pass
    prof = st.get("network_profile")
    if prof and prof.get("was") and prof.get("interface_index") is not None:
        c_run(ps("Set-NetConnectionProfile -InterfaceIndex %d -NetworkCategory %s" % (int(prof["interface_index"]), prof["was"])))
        done.append("نوع الشبكة أُعيد إلى %s" % prof["was"])
    try:
        os.remove(state_path())
    except OSError:
        pass
    done.append("لم يُلمس البرنامج ولا القاعدة ولا الأرشيف.")
    return done
