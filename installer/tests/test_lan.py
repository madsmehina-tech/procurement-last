# -*- coding: utf-8 -*-
"""The network part of PO Setup (main PC + team PCs): tested without Windows; CI runs the real thing on a Windows machine."""
import http.server
import json
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import po_lan as lan  # noqa: E402
import po_setup as ps  # noqa: E402
from test_installer import answers, Recorder  # noqa: E402


def test_remote_list_accepts_ranges_and_refuses_the_whole_internet():
    ok, errs = lan.parse_remote_list("192.168.5.0/24, 10.0.0.0/8 LocalSubnet")
    assert ok == ["192.168.5.0/24", "10.0.0.0/8"] and not errs
    _o, errs = lan.parse_remote_list("0.0.0.0/0")
    assert errs and "الإنترنت" in errs[0]
    _o, errs = lan.parse_remote_list("not-an-ip")
    assert errs


def test_alias_rule():
    assert lan.valid_alias("poweb") and lan.valid_alias("po-web2") and lan.valid_alias("alhadab.procurement") and lan.valid_alias("po.alhadab.lan")
    assert not lan.valid_alias("PO web") and not lan.valid_alias("a") and not lan.valid_alias("بو")
    assert not lan.valid_alias(".po") and not lan.valid_alias("po.") and not lan.valid_alias("po..web") and not lan.valid_alias("-po") and not lan.valid_alias("po-.web")


def test_hosts_only_our_tagged_lines_are_added_replaced_and_removed(tmp_path):
    h = tmp_path / "hosts"
    h.write_text("127.0.0.1 localhost\r\n10.0.0.5 other  # mine\r\n", encoding="utf-8")
    assert lan.hosts_set(str(h), "192.168.100.55", "poweb") is True
    assert lan.hosts_set(str(h), "192.168.100.55", "poweb") is False                    # nothing changed the second time
    lan.hosts_set(str(h), "192.168.100.77", "poweb")                                  # an address change REPLACES our line
    text = h.read_text(encoding="utf-8")
    assert text.count("poweb") == 1 and "192.168.100.77" in text and "192.168.100.55" not in text
    assert "10.0.0.5 other  # mine" in text and "127.0.0.1 localhost" in text           # theirs untouched
    assert os.path.isfile(str(h) + ".po_setup.bak")
    assert lan.hosts_remove(str(h)) == 1
    assert "poweb" not in h.read_text(encoding="utf-8") and "10.0.0.5 other" in h.read_text(encoding="utf-8")


def serve_health(status_ok=True):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if status_ok else 500)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_connection_test_says_which_layer_fails():
    srv = serve_health()
    try:
        ok, msg = lan.check_connection("127.0.0.1", srv.server_address[1])
        assert ok and "سليم" in msg
    finally:
        srv.shutdown()
    ok, msg = lan.check_connection("127.0.0.1", 9, timeout=1)                          # nothing listens on port 9
    assert not ok and "لا يصل" in msg and "Public" in msg
    srv = serve_health(status_ok=False)
    try:
        ok, msg = lan.check_connection("127.0.0.1", srv.server_address[1])
        assert not ok and "/health" in msg
    finally:
        srv.shutdown()


def test_import_file_is_checked_and_applied(tmp_path):
    f = tmp_path / "client_import.json"
    f.write_text(json.dumps({"product": "po_web", "server_ip": "192.168.100.55", "port": 5000, "alias": "poweb", "tailscale_ip": "100.80.1.2"}), encoding="utf-8")
    a = lan.apply_import(lan.ClientAnswers(), lan.load_import(str(f)))
    assert (a.server, a.port, a.alias, a.tailscale_ip) == ("192.168.100.55", 5000, "poweb", "100.80.1.2")
    bad = tmp_path / "x.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        lan.load_import(str(bad))
    a.outside_office = True
    assert lan.client_target(a) == "100.80.1.2"                                         # outside the office the name must point to the Tailscale address


def run_client(tmp_path, a, ok=True, admin=True):
    desktop = tmp_path / "Desktop"
    desktop.mkdir(exist_ok=True)
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\r\n", encoding="utf-8")
    logs = []
    res, overall = lan.run_client(a, logs.append, ps.ps, ps.ps_quote, admin, desktop=str(desktop), hosts=str(hosts), tester=lambda h, p: (ok, "msg %s:%s" % (h, p)))
    return res, overall, desktop, hosts


def test_client_happy_path_makes_alias_and_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", alias="poweb", open_browser=False)
    res, overall, desktop, hosts = run_client(tmp_path, a)
    assert overall == "ok", res
    url = (desktop / "نظام المشتريات.url").read_text(encoding="utf-8")
    assert "URL=http://poweb:5000" in url
    assert "192.168.100.55\tpoweb" in hosts.read_text(encoding="utf-8")
    assert lan.load_state(str(tmp_path / "state.json"))["shortcuts"]


def test_client_without_admin_falls_back_to_the_numeric_address(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", alias="poweb", open_browser=False)
    res, overall, desktop, hosts = run_client(tmp_path, a, admin=False)
    assert overall == "warn"
    assert "URL=http://192.168.100.55:5000" in (desktop / "نظام المشتريات.url").read_text(encoding="utf-8")
    assert "poweb" not in hosts.read_text(encoding="utf-8")


def test_client_stops_when_the_server_cannot_be_reached_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", alias="poweb", open_browser=False)
    res, overall, desktop, hosts = run_client(tmp_path, a, ok=False)
    assert overall == "fail" and [r[0] for r in res] == ["test"]
    assert not list(desktop.iterdir()) and "poweb" not in hosts.read_text(encoding="utf-8")


def test_remove_undoes_only_ours(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", alias="poweb", open_browser=False)
    _r, _o, desktop, hosts = run_client(tmp_path, a)
    cmds = []
    lines = lan.remove_lan(lambda cmd: (cmds.append(cmd) or (0, "")), ps.ps, hosts=str(hosts), state={"shortcuts": [str(desktop / "نظام المشتريات.url")],
                                                                                                         "network_profile": {"interface_index": 4, "was": "Public"}})
    assert not list(desktop.iterdir()) and "poweb" not in hosts.read_text(encoding="utf-8") and "localhost" in hosts.read_text(encoding="utf-8")
    flat = [" ".join(map(str, c)) for c in cmds]
    assert any("delete rule name=PO Web (LAN)" in c for c in flat) and any("Set-NetConnectionProfile -InterfaceIndex 4 -NetworkCategory Public" in c for c in flat)
    assert any("لم يُلمس البرنامج" in l for l in lines)


def test_host_firewall_includes_extra_ranges_and_the_exports_file_has_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(lan, "detect_ip", lambda: "192.168.100.55")
    (tmp_path / "mine.accdb").write_bytes(b"MYDB")
    a = answers(tmp_path, db_mode="existing", db_existing=str(tmp_path / "mine.accdb"), open_firewall=True, allowed_extra="192.168.5.0/24", alias_name="poweb", export_dir=str(tmp_path / "out"))
    rec = Recorder()
    ps.run_install(a, lambda m: None, run=rec)
    flat = [" ".join(str(x) for x in call[0]) for call in rec.calls]
    fw = [c for c in flat if "PO Web (LAN)" in c and " add " in c]
    assert fw and "remoteip=LocalSubnet,192.168.5.0/24" in fw[0]
    data = json.loads((tmp_path / "out" / "client_import.json").read_text(encoding="utf-8"))
    assert data["server_ip"] == "192.168.100.55" and data["port"] == 5000 and data["alias"] == "poweb"
    assert "password" not in json.dumps(data).lower()


def test_validate_rejects_open_internet_and_bad_alias(tmp_path):
    a = answers(tmp_path, allowed_extra="0.0.0.0/0", alias_name="Bad Name")
    errs = ps.validate(a, check_paths=False)
    assert any("الإنترنت" in e for e in errs) and any("الاسم البديل" in e for e in errs)


def test_a_dotted_alias_is_written_to_hosts_and_removed(tmp_path):
    h = tmp_path / "hosts"
    h.write_text("127.0.0.1 localhost\r\n", encoding="utf-8")
    assert lan.hosts_set(str(h), "192.168.100.55", "alhadab.procurement")
    assert "192.168.100.55\talhadab.procurement\t# PO-SETUP" in h.read_text(encoding="utf-8")
    assert lan.hosts_remove(str(h)) == 1 and "alhadab" not in h.read_text(encoding="utf-8")


def test_client_saves_the_main_pc_account_for_every_name_and_remove_deletes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", server_name="ENG-PC", alias="alhadab.procurement", open_browser=False, share_user="po", share_password="S3cret!")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text("", encoding="utf-8")
    calls, logs = [], []
    res, overall = lan.run_client(a, logs.append, ps.ps, ps.ps_quote, True, desktop=str(desktop), hosts=str(hosts), tester=lambda h, p: (True, "ok"),
                                  cred_runner=lambda cmd: (calls.append(cmd) or 0))
    assert overall == "ok", res
    assert [c[1] for c in calls] == ["/add:192.168.100.55", "/add:ENG-PC", "/add:alhadab.procurement"]
    assert all(c[2] == "/user:ENG-PC\\po" and c[3] == "/pass:S3cret!" for c in calls)
    assert "S3cret!" not in "\n".join(logs) and "S3cret!" not in (tmp_path / "state.json").read_text(encoding="utf-8")      # never logged, never stored by the tool
    cmds = []
    lan.remove_lan(lambda cmd: (cmds.append(cmd) or (0, "")), ps.ps, hosts=str(hosts))
    assert ["cmdkey", "/delete:ENG-PC"] in cmds and ["cmdkey", "/delete:192.168.100.55"] in cmds


def test_client_with_a_user_but_no_password_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", open_browser=False, share_user="po", shortcut=False)
    calls = []
    res, overall = lan.run_client(a, lambda m: None, ps.ps, ps.ps_quote, True, tester=lambda h, p: (True, "ok"), cred_runner=lambda cmd: (calls.append(cmd) or 0))
    assert overall == "warn" and calls == []


def test_boot_task_as_system_and_as_a_windows_account(tmp_path, monkeypatch):
    (tmp_path / "mine.accdb").write_bytes(b"MYDB")
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = answers(tmp_path, db_mode="existing", db_existing=str(tmp_path / "mine.accdb"), boot_start=True)
    rec = Recorder()
    res, overall, _c = ps.run_install(a, lambda m: None, run=rec)
    flat = [" ".join(str(x) for x in c[0]) for c in rec.calls]
    reg = [c for c in flat if "Register-ScheduledTask" in c]
    assert len(reg) == 1 and "-UserId 'SYSTEM'" in reg[0] and "AtStartup" in reg[0] and "PO Web Server (Boot)" in reg[0]
    assert not any("install_autostart.ps1" in c for c in flat)                                 # not the «after sign-in» task as well

    a2 = answers(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, db_mode="existing", db_existing=str(tmp_path / "mine.accdb"), boot_start=True, boot_user="svc", boot_password="W1nPass!")
    rec, log = Recorder(), []
    res, overall, _c = ps.run_install(a2, log.append, run=rec)
    reg = [c for c in rec.calls if "Register-ScheduledTask" in " ".join(map(str, c[0]))][0]
    text = " ".join(map(str, reg[0]))
    assert "-Password $env:PO_TASK_PASSWORD" in text and "W1nPass!" not in text and reg[2]["PO_TASK_PASSWORD"] == "W1nPass!"   # in the child's environment only
    assert "\\svc'" in text and "-RunLevel Limited" in text
    assert "W1nPass!" not in "\n".join(map(str, log))
    assert any(c[0][0] == "icacls" and c[0][2] == "/grant" and c[0][3].endswith("\\svc:(OI)(CI)M") for c in rec.calls)       # the account can write the data


def test_boot_account_rules(tmp_path):
    assert ps.boot_account("mahmoud").endswith("\\mahmoud") and ps.boot_account(".\\d").endswith("\\d") and ps.boot_account("CORP\\x") == "CORP\\x"
    assert ps.python_private_to(r"C:\Users\D\AppData\Local\Programs\Python\Python312\python.exe", "PC\\svc")
    assert not ps.python_private_to(r"C:\Users\D\AppData\Local\Programs\Python\Python312\python.exe", "PC\\d")
    assert not ps.python_private_to(r"C:\Program Files\Python312\python.exe", "PC\\svc")
    assert any("كلمة مرور ويندوز" in e for e in ps.validate(answers(tmp_path, boot_start=True, boot_user="svc"), check_paths=False))


def test_user_steps_run_in_the_desk_users_session_and_are_recorded_for_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", server_name="ENG-PC", share_name="PO_Archive", open_browser=False, share_user="po", share_password="S3c'ret", map_drive="p:")
    seen = []

    def runner(script):
        seen.append(script)
        return "\ufeffCRED=192.168.100.55|0\r\nCRED=ENG-PC|0\r\nDRIVE=P|0|The command completed successfully.\r\nLNK=C:\\Users\\sara\\Desktop\\x.url\r\n", "desk-user"
    logs = []
    res, overall = lan.run_client(a, logs.append, ps.ps, ps.ps_quote, False, tester=lambda h, p: (True, "ok"), user_runner=runner)
    assert overall == "ok", res
    sc = seen[0]
    assert "net use P: " in sc and "\\\\192.168.100.55\\PO_Archive" in sc and "/persistent:yes" in sc and "$p = 'S3c''ret'" in sc and "/user:$u" in sc
    assert "S3c" not in "\n".join(logs) and "S3c" not in (tmp_path / "state.json").read_text(encoding="utf-8")
    uc = lan.load_state(str(tmp_path / "state.json"))["user_ctx"]
    assert uc["drive"] == "P" and uc["cmdkeys"] == ["192.168.100.55", "ENG-PC"] and uc["shortcuts"] == ["C:\\Users\\sara\\Desktop\\x.url"]
    assert [r[0] for r in res if r[0] in ("cred", "drive", "shortcut")] == ["cred", "drive", "shortcut"]
    undo = lan.undo_user_script(uc, "R")
    assert "cmdkey /delete:ENG-PC" in undo and "net use P: /delete /y" in undo and "x.url" in undo


def test_a_drive_that_cannot_be_mapped_is_reported_with_the_windows_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "state_path", lambda: str(tmp_path / "state.json"))
    a = lan.ClientAnswers(server="192.168.100.55", open_browser=False, map_drive="P", shortcut=False)
    res, overall = lan.run_client(a, lambda m: None, ps.ps, ps.ps_quote, False, tester=lambda h, p: (True, "ok"),
                                  user_runner=lambda sc: ("DRIVE=P|2|System error 1326 has occurred. The user name or password is incorrect.", "direct"))
    drive = [r for r in res if r[0] == "drive"][0]
    assert overall == "warn" and drive[2] == "warn" and "1326" in drive[3]
    assert lan.drive_letter("p:") == "P" and lan.drive_letter("PO") == "" and lan.drive_letter("") == ""
    res, overall = lan.run_client(lan.ClientAnswers(server="1.2.3.4", open_browser=False), lambda m: None, ps.ps, ps.ps_quote, False, tester=lambda h, p: (True, "ok"), user_runner=lambda sc: (None, "failed"))
    assert overall == "warn" and any(r[0] == "user" for r in res)
