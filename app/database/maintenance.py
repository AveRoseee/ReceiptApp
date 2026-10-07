"""Coordinate file replacement with connections owned by this process."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time

_condition = threading.Condition()
_active = {}
_owners = {}
RESTORE_JOURNAL = ".restore-journal.json"


def cleanup_directory(directory, parent):
    directory, parent = Path(directory), Path(parent).resolve()
    resolved = directory.resolve()
    if resolved == parent or not resolved.is_relative_to(parent) or directory.is_symlink():
        raise ValueError("Folder sementara tidak valid.")
    if directory.exists():
        shutil.rmtree(directory)


def recover_restore(database_path):
    """Recover the file swap before opening SQLite; no service dependency."""
    database_path = Path(database_path).resolve()
    base, journal = database_path.parent, database_path.parent / RESTORE_JOURNAL
    if not journal.exists():
        return
    data = json.loads(journal.read_text(encoding="utf-8"))
    name = data["stage"]
    if not re.fullmatch(r"\.restore-[0-9a-f]{32}", name):
        raise ValueError("Jurnal pemulihan tidak valid.")
    stage = base / name
    if not stage.resolve().is_relative_to(base) or stage.is_symlink():
        raise ValueError("Folder pemulihan tidak valid.")
    if not data["committed"]:
        if (stage / "old.db").exists():
            os.replace(stage / "old.db", database_path)
        if (stage / "old-assets").exists():
            if (base / "assets").exists():
                cleanup_directory(base / "assets", base)
            os.replace(stage / "old-assets", base / "assets")
        elif not data["had_assets"] and not (stage / "incoming/assets").exists() and (base / "assets").exists():
            cleanup_directory(base / "assets", base)
    journal.unlink()
    cleanup_directory(stage, base)


def key(path):
    return str(Path(path).resolve()).casefold() if str(path) != ":memory:" else None


def register(path):
    name, thread = key(path), threading.get_ident()
    if name is None:
        return None
    with _condition:
        until = time.monotonic() + 5
        while name in _owners and _owners[name] != thread:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise sqlite3.OperationalError("Data sedang dicadangkan/dipulihkan. Coba kembali.")
            _condition.wait(remaining)
        _active.setdefault(name, {})[thread] = _active.get(name, {}).get(thread, 0) + 1
    return name, thread


def unregister(token):
    if token is None:
        return
    name, thread = token
    with _condition:
        _active[name][thread] -= 1
        if not _active[name][thread]:
            del _active[name][thread]
        if not _active[name]:
            del _active[name]
        _condition.notify_all()


@contextmanager
def exclusive(path):
    name, thread = key(path), threading.get_ident()
    with _condition:
        if name in _owners or _active.get(name, {}).get(thread):
            raise sqlite3.OperationalError("Tutup operasi data sebelum backup/restore.")
        _owners[name] = thread
        until = time.monotonic() + 5
        while _active.get(name):
            remaining = until - time.monotonic()
            if remaining <= 0:
                del _owners[name]
                _condition.notify_all()
                raise sqlite3.OperationalError("Masih ada operasi data berjalan. Coba kembali.")
            _condition.wait(remaining)
    try:
        yield
    finally:
        with _condition:
            del _owners[name]
            _condition.notify_all()
