# -*- coding: utf-8 -*-
"""Full export of an installation into one package, and restoring it into a new one: nothing lost, nothing overwritten, every file checked."""
import json
import os
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import po_setup as ps  # noqa: E402
import po_transfer as tr  # noqa: E402
from test_installer import answers, Recorder  # noqa: E402


def old_install(tmp_path, legacy_relative=True):
    base = tmp_path / "old"
    app = base / "app"
    (app / "instance").mkdir(parents=True)
    (app / "instance" / "secret.key").write_bytes(b"KEY")
    (base / "data").mkdir()
    (base / "data" / "PurchaseSystem.accdb").write_bytes(b"ACCESS-DB-CONTENT")
    arch = tmp_path / "share" / "PO_Archive"
    (arch / "الموردين" / "مورد" / "PO-1").mkdir(parents=True)
    (arch / "الموردين" / "مورد" / "PO-1" / "أمر الشراء.pdf").write_bytes(b"%PDF-1")
    (arch / "a.pdf").write_bytes(b"%PDF-2")
    (app / "uploads" / "contracts").mkdir(parents=True)
    (app / "uploads" / "contracts" / "old.pdf").write_bytes(b"%PDF-3")
    (app / "config.ini").write_text("[database]\npath = %s\n[storage]\narchive_root = %s   ; the share\nlegacy_uploads = uploads\n" % (base / "data" / "PurchaseSystem.accdb", arch), encoding="utf-8")
    return app, arch


def test_the_paths_come_from_config_ini_and_relative_ones_from_the_program_folder(tmp_path):
    app, arch = old_install(tmp_path)
    info = tr.read_install(str(app))
    assert info["archive"] == str(arch) and info["legacy"] == os.path.join(str(app), "uploads") and info["db"].endswith("PurchaseSystem.accdb")
    with pytest.raises(tr.TransferError):
        tr.read_install(str(tmp_path))


def test_export_holds_everything_is_verified_and_changes_nothing(tmp_path):
    app, arch = old_install(tmp_path)
    before = {p: open(p, "rb").read() for p, _n in tr.plan_export(str(app))[1]}
    r = tr.export_package(str(app), str(tmp_path / "usb"))
    assert r["counts"] == {"db": 1, "archive": 2, "legacy": 1, "instance": 1} and os.path.isfile(r["package"]) and not os.path.exists(r["package"] + ".part")
    with zipfile.ZipFile(r["package"]) as z:
        names = set(z.namelist())
    assert {"db/PurchaseSystem.accdb", "archive/a.pdf", "archive/الموردين/مورد/PO-1/أمر الشراء.pdf", "legacy/contracts/old.pdf", "instance/secret.key", "reference/config.ini", "manifest.json"} <= names
    assert tr.verify_package(r["package"])["ok"]
    assert all(open(p, "rb").read() == data for p, data in before.items())                # the installation is untouched


def test_export_refuses_an_open_database_and_a_destination_inside_the_data(tmp_path):
    app, arch = old_install(tmp_path)
    lock = tmp_path / "old" / "data" / "PurchaseSystem.laccdb"
    lock.write_bytes(b"")
    with pytest.raises(tr.TransferError, match="أوقف الخادم"):
        tr.export_package(str(app), str(tmp_path / "usb"))
    lock.unlink()
    with pytest.raises(tr.TransferError, match="داخل بيانات النظام"):
        tr.export_package(str(app), str(arch / "backup"))
    assert not os.path.exists(str(arch / "backup"))


def test_a_damaged_package_is_detected(tmp_path):
    app, _a = old_install(tmp_path)
    pkg = tr.export_package(str(app), str(tmp_path / "usb"))["package"]
    data = bytearray(open(pkg, "rb").read())
    i = data.find(b"ACCESS-DB-CONTENT")
    data[i] ^= 0xFF
    open(pkg, "wb").write(bytes(data))
    assert not tr.verify_package(pkg)["ok"]
    with pytest.raises(tr.TransferError):
        tr.read_manifest(str(tmp_path / "old" / "app" / "config.ini"))                       # not a package at all
    dest = tmp_path / "new"
    r = tr.restore_package(pkg, str(dest / "data" / "db.accdb"), str(dest / "arch"), str(dest / "up"), str(dest / "inst"))
    assert not r["ok"] and r["damaged"] == ["db/PurchaseSystem.accdb"] and not os.path.exists(str(dest / "data" / "db.accdb"))   # a damaged database is never put in place


def test_restore_puts_everything_in_the_new_places_and_never_overwrites(tmp_path):
    app, _a = old_install(tmp_path)
    pkg = tr.export_package(str(app), str(tmp_path / "usb"))["package"]
    new = tmp_path / "new"
    db, arch, up, inst = str(new / "data" / "PurchaseSystem.accdb"), str(new / "Archive2"), str(new / "uploads"), str(new / "app" / "instance")
    r = tr.restore_package(pkg, db, arch, up, inst)
    assert r["ok"] and r["written"] == 5
    assert open(db, "rb").read() == b"ACCESS-DB-CONTENT"
    assert open(os.path.join(arch, "الموردين", "مورد", "PO-1", "أمر الشراء.pdf"), "rb").read() == b"%PDF-1"
    assert open(os.path.join(up, "contracts", "old.pdf"), "rb").read() == b"%PDF-3" and open(os.path.join(inst, "secret.key"), "rb").read() == b"KEY"
    with pytest.raises(tr.TransferError, match="لا يكتب فوق قاعدة"):                          # a second restore over it is refused
        tr.restore_package(pkg, db, arch, up, inst)
    other = tmp_path / "other"
    (other / "arch").mkdir(parents=True)
    (other / "arch" / "a.pdf").write_bytes(b"DIFFERENT")
    with pytest.raises(tr.TransferError, match="بمحتوى مختلف"):
        tr.restore_package(pkg, str(other / "db.accdb"), str(other / "arch"), str(other / "up"))
    assert open(str(other / "arch" / "a.pdf"), "rb").read() == b"DIFFERENT" and not os.path.exists(str(other / "db.accdb"))    # nothing was written at all


def test_a_package_cannot_write_outside_its_folders(tmp_path):
    pkg = tmp_path / "evil.zip"
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr("archive/../../escape.txt", b"x")
        import hashlib
        z.writestr("manifest.json", json.dumps({"product": "po_web", "format": 1, "files": [{"name": "archive/../../escape.txt", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(), "mtime": 0}]}))
    new = tmp_path / "n"
    tr.restore_package(str(pkg), str(new / "d.accdb"), str(new / "arch"), str(new / "up"))
    assert not os.path.exists(str(tmp_path / "escape.txt")) and os.path.isfile(str(new / "arch" / "escape.txt"))               # «..» is dropped: it stays inside


def test_the_installer_restores_a_package_into_a_new_installation(tmp_path):
    app, _a = old_install(tmp_path)
    pkg = tr.export_package(str(app), str(tmp_path / "usb"))["package"]
    (tmp_path / "n").mkdir()
    a = answers(tmp_path / "n", db_mode="restore", restore_package=pkg, admin_password="")
    assert ps.validate(a) == []
    rec = Recorder()
    results, overall, _c = ps.run_install(a, lambda m: None, run=rec)
    assert overall in ("ok", "warn"), results
    assert open(a.db_path, "rb").read() == b"ACCESS-DB-CONTENT"
    assert os.path.isfile(os.path.join(a.share_folder, "a.pdf")) and os.path.isfile(os.path.join(a.uploads, "contracts", "old.pdf"))
    migrate = [c for c in rec.calls if "migrate.py" in " ".join(map(str, c[0]))][0]
    assert "--new-database" not in migrate[0]                                                # a restored database is migrated like an existing one
    a.replace_existing = True                                                                # (a second run is an upgrade of the program)
    again, overall2, _c = ps.run_install(a, lambda m: None, run=Recorder())                  # running the installer again leaves the restored data alone
    assert overall2 in ("ok", "warn") and "لم يُلمس" in [r for r in again if r[0] == "database"][0][3]
    assert any("حزمة الاسترداد" in e for e in ps.validate(answers(tmp_path / "n", db_mode="restore", restore_package="nope.zip")))


def test_export_text_reports_problems_in_arabic(tmp_path):
    assert ps.export_text("", "").startswith("✘")
    assert ps.export_text(str(tmp_path), str(tmp_path / "x")).startswith("✘")
    app, _a = old_install(tmp_path)
    assert ps.export_text(str(app), str(tmp_path / "usb")).startswith("✔")
