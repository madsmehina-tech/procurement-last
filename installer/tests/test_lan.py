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
