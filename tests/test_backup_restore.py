from contextlib import closing
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from app.database import connect, initialize_database
from app.services import backup_service as backups
from app.services.business_profile_service import save_business_profile, get_business_profile
from test_core_invoice import business


def name(path):
    with closing(connect(path)) as db:
        return get_business_profile(db)["name"]


def edit(path):
    with closing(connect(path)) as db:
        save_business_profile(db, {"name": "Sebelum Restore"})


def test_backup_restore_assets_manifest_and_safety_copy(business):
    path, _ = business
    (path.parent / "assets").mkdir()
    asset = path.parent / "assets/example.txt"
    asset.write_text("awal")
    archive = backups.create_backup(path, path.parent / "backup.zip")
    with ZipFile(archive) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["format_version"] == 1
        assert manifest["database_schema_version"] == 1
        assert manifest["app_name"] == "DokumenUsaha"
        assert "database.sqlite3" in z.namelist()
        assert z.read("assets/example.txt") == b"awal"
    edit(path)
    asset.write_text("baru")
    safety = backups.restore_backup(path, archive)
    assert name(path) == "Usaha Awal"
    assert asset.read_text() == "awal"
    assert safety.is_file()
    backups.restore_backup(path, safety)
    assert name(path) == "Sebelum Restore"
    assert asset.read_text() == "baru"


@pytest.mark.parametrize("bad_name", ["../escape", "assets/../../escape", "C:/escape", "assets\\escape", "assets/CON.txt"])
def test_path_traversal_rejected_without_touching_live_database(business, bad_name):
    path, _ = business
    archive = path.parent / "bad.zip"
    with ZipFile(archive, "w") as z:
        z.writestr(bad_name, b"bad")
    before = path.read_bytes()
    with pytest.raises(backups.BackupValidationError):
        backups.restore_backup(path, archive)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".restore-*"))


@pytest.mark.parametrize("change", ["manifest", "manifest_list", "manifest_null", "checksum", "database", "schema"])
def test_invalid_archive_is_rejected(business, change):
    path, _ = business
    original = backups.create_backup(path, path.parent / "valid.zip")
    with ZipFile(original) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    if change == "manifest":
        entries["manifest.json"] = b"{}"
    elif change == "manifest_list":
        entries["manifest.json"] = b"[]"
    elif change == "manifest_null":
        entries["manifest.json"] = b"null"
    elif change == "database":
        entries["database.sqlite3"] = b"broken SQLite"
    else:
        manifest = json.loads(entries["manifest.json"])
        if change == "schema":
            manifest["database_schema_version"] = 99
        else:
            manifest["files"]["database.sqlite3"]["sha256"] = "0" * 64
        entries["manifest.json"] = json.dumps(manifest).encode()
    bad = path.parent / "bad.zip"
    with ZipFile(bad, "w", ZIP_DEFLATED) as z:
        for n, data in entries.items():
            z.writestr(n, data)
    before = path.read_bytes()
    with pytest.raises(backups.BackupValidationError):
        backups.restore_backup(path, bad)
    assert path.read_bytes() == before


def test_mid_restore_failure_rolls_back_database_and_assets(business, monkeypatch):
    path, _ = business
    (path.parent / "assets").mkdir()
    asset = path.parent / "assets/example.txt"
    asset.write_text("awal")
    archive = backups.create_backup(path, path.parent / "backup.zip")
    edit(path)
    asset.write_text("baru")
    original = backups.os.replace
    failed = False
    def fail_once(src, dst):
        nonlocal failed
        if Path(src).name == "assets" and Path(src).parent.name == "incoming" and not failed:
            failed = True
            raise OSError("simulated disk failure")
        return original(src, dst)
    monkeypatch.setattr(backups.os, "replace", fail_once)
    with pytest.raises(OSError):
        backups.restore_backup(path, archive)
    assert failed
    assert name(path) == "Sebelum Restore"
    assert asset.read_text() == "baru"
    assert list((path.parent / "backups").glob("*.zip"))
    assert not (path.parent / backups.JOURNAL).exists()


def test_restore_does_not_close_callers_connection(business):
    path, _ = business
    archive = backups.create_backup(path, path.parent / "backup.zip")
    with closing(connect(path)) as db:
        with pytest.raises(sqlite3.OperationalError):
            backups.restore_backup(path, archive)
        assert db.execute("SELECT 1").fetchone()[0] == 1


def test_startup_recovers_interrupted_swap(business):
    path, _ = business
    stage = path.parent / (".restore-" + "a" * 32)
    stage.mkdir()
    (stage / "incoming").mkdir()
    backups._journal(path.parent / backups.JOURNAL, {
        "stage": stage.name, "had_assets": False, "committed": False,
    })
    backups.os.replace(path, stage / "old.db")
    path.write_bytes(b"interrupted replacement")
    initialize_database(path)
    assert name(path) == "Usaha Awal"
    assert not stage.exists()


def test_unknown_trigger_is_not_trusted(business):
    path, _ = business
    with closing(connect(path)) as db:
        db.execute("CREATE TRIGGER unexpected AFTER INSERT ON customers BEGIN SELECT 1; END")
    with pytest.raises(ValueError):
        backups.create_backup(path, path.parent / "bad-schema.zip")
