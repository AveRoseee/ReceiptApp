"""Offline ZIP backup and replace-all restore with rollback journal."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from app.database.maintenance import (
    exclusive, recover_restore, cleanup_directory as _cleanup,
    RESTORE_JOURNAL as JOURNAL,
)
from app.repositories import backup_repository as repository

APP_NAME = "DokumenUsaha"
FORMAT_VERSION = 1
MAX_TOTAL = 1024 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
MAX_FILES = 10000


class BackupValidationError(ValueError):
    pass


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise BackupValidationError("Path dalam cadangan tidak valid.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {".", ".."} for part in name.split("/")):
        raise BackupValidationError("Path dalam cadangan keluar dari folder data.")
    for part in path.parts:
        if part.endswith((" ", ".")) or re.search(r'[<>"|?*\x00-\x1f]', part):
            raise BackupValidationError("Nama file cadangan tidak didukung Windows.")
        if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part):
            raise BackupValidationError("Nama file cadangan tidak aman.")
    return path


def _validate_data(directory):
    version, references = repository.validate_database((directory / "database.sqlite3").resolve())
    for name in references:
        relative = _safe_name(name)
        if not relative.parts or relative.parts[0] != "assets":
            raise BackupValidationError("Referensi gambar harus berada di assets.")
        path = (directory / str(relative)).resolve()
        if not path.is_relative_to(directory.resolve() / "assets") or not path.is_file():
            raise BackupValidationError("Gambar yang dirujuk database tidak ada dalam cadangan.")
    return version


def _archive(database_path, output_path):
    output_path = Path(output_path).resolve()
    base = database_path.parent
    if output_path.suffix.lower() != ".zip" or output_path.is_relative_to(base / "assets") or output_path == database_path:
        raise BackupValidationError("Pilih file ZIP di luar folder assets.")
    with tempfile.TemporaryDirectory(prefix="dokumenusaha-backup-") as temp:
        directory = Path(temp)
        repository.snapshot_database(database_path, directory / "database.sqlite3")
        asset_root = base / "assets"
        if asset_root.exists():
            if asset_root.is_symlink() or asset_root.is_junction():
                raise BackupValidationError("Folder assets tidak boleh berupa tautan.")
            for source in asset_root.rglob("*"):
                if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(asset_root.resolve()):
                    raise BackupValidationError("Folder gambar mengandung tautan yang tidak didukung.")
                relative = source.relative_to(base)
                target = directory / relative
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        version = _validate_data(directory)
        files = {}
        total = 0
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                size = path.stat().st_size
                total += size
                if size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
                    raise BackupValidationError("Data terlalu besar untuk format cadangan ini.")
                files[path.relative_to(directory).as_posix()] = {"size": size, "sha256": sha256(path.read_bytes()).hexdigest()}
        manifest = {"format_version": FORMAT_VERSION, "app_name": APP_NAME,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "database_schema_version": version, "files": files}
        temporary = output_path.parent / ("." + uuid4().hex + ".zip")
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                archive.writestr("assets/", b"")
                for name in files:
                    archive.write(directory / name, name)
            os.replace(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)
    return output_path


def create_backup(database_path, output_path):
    database_path = Path(database_path).resolve()
    if not database_path.is_file():
        raise BackupValidationError("Database tidak ditemukan.")
    with exclusive(database_path):
        return _archive(database_path, output_path)


def _extract(archive_path, directory):
    try:
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 100 or sum(e.file_size for e in entries) > MAX_TOTAL:
                raise BackupValidationError("Cadangan terlalu besar.")
            names = set()
            for entry in entries:
                name = entry.filename.rstrip("/")
                _safe_name(name)
                if name.casefold() in names:
                    raise BackupValidationError("Cadangan memiliki nama file ganda.")
                names.add(name.casefold())
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise BackupValidationError("Tautan/file terenkripsi tidak didukung.")
                if entry.file_size > MAX_FILE or not (name in {"manifest.json", "database.sqlite3", "assets"} or name.startswith("assets/")):
                    raise BackupValidationError("Struktur cadangan tidak valid.")
            info = archive.getinfo("manifest.json")
            if info.file_size > 1024 * 1024:
                raise BackupValidationError("Manifest terlalu besar.")
            manifest = json.loads(archive.read(info))
            if (not isinstance(manifest, dict)
                    or type(manifest.get("format_version")) is not int or manifest["format_version"] != FORMAT_VERSION
                    or manifest.get("app_name") != APP_NAME or not isinstance(manifest.get("files"), dict)):
                raise BackupValidationError("Format cadangan tidak didukung.")
            datetime.fromisoformat(manifest["created_at"])
            actual = {e.filename for e in entries if not e.is_dir() and e.filename != "manifest.json"}
            if actual != set(manifest["files"]) or "database.sqlite3" not in actual:
                raise BackupValidationError("Isi cadangan tidak sesuai manifest.")
            for entry in entries:
                target = directory / entry.filename
                if not target.resolve().is_relative_to(directory.resolve()):
                    raise BackupValidationError("Path cadangan tidak aman.")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if entry.filename == "manifest.json":
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = sha256()
                size = 0
                with archive.open(entry) as src, target.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise BackupValidationError("File cadangan terlalu besar.")
                        digest.update(chunk)
                        dst.write(chunk)
                expected = manifest["files"][entry.filename]
                if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                    raise BackupValidationError("Checksum cadangan tidak cocok.")
            version = _validate_data(directory)
            if type(manifest.get("database_schema_version")) is not int or version != manifest["database_schema_version"]:
                raise BackupValidationError("Versi schema cadangan tidak cocok.")
    except (BadZipFile, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        if isinstance(error, BackupValidationError):
            raise
        raise BackupValidationError("Cadangan rusak atau bukan cadangan DokumenUsaha yang didukung.") from error


def _journal(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def restore_backup(database_path, archive_path):
    database_path = Path(database_path).resolve()
    base = database_path.parent
    with exclusive(database_path):
        recover_restore(database_path)
        stage = base / (".restore-" + uuid4().hex)
        stage.mkdir()
        incoming = stage / "incoming"
        incoming.mkdir()
        journal = base / JOURNAL
        try:
            _extract(archive_path, incoming)
            (incoming / "assets").mkdir(exist_ok=True)
            # Every application connection has closed before the replacement.
            backups = base / "backups"
            backups.mkdir(exist_ok=True)
            safety = _archive(database_path, backups / ("before-restore-" + uuid4().hex + ".zip"))
            if any(Path(str(database_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                raise BackupValidationError("Tutup proses lain yang memakai database sebelum pemulihan.")
            data = {"stage": stage.name, "had_assets": (base / "assets").exists(), "committed": False}
            _journal(journal, data)
            os.replace(database_path, stage / "old.db")
            if data["had_assets"]:
                os.replace(base / "assets", stage / "old-assets")
            os.replace(incoming / "database.sqlite3", database_path)
            os.replace(incoming / "assets", base / "assets")
            data["committed"] = True
            _journal(journal, data)
            recover_restore(database_path)
            return safety
        except BaseException:
            if journal.exists():
                recover_restore(database_path)
            elif stage.exists():
                _cleanup(stage, base)
            raise
