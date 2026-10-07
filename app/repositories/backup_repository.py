"""SQLite-only backup and validation operations."""
from contextlib import closing
from functools import lru_cache
import json
import sqlite3

from app.database import connect
from app.database.database import migrate


def snapshot_database(source, destination):
    with closing(connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def _schema(db):
    return sorted(tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


@lru_cache(maxsize=1)
def _expected():
    with closing(connect(":memory:")) as db:
        migrate(db)
        history = [tuple(r) for r in db.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version")]
        return _schema(db), history


def validate_database(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database cadangan rusak.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Relasi database cadangan tidak valid.")
        schema, history = _expected()
        actual_history = [tuple(r) for r in db.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version")]
        if _schema(db) != schema or actual_history != history:
            raise ValueError("Schema cadangan tidak sesuai versi aplikasi.")
        references = []
        def inspect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"logo_path", "qris_path", "signature_path", "stamp_path"} and item:
                        references.append(item)
                    else:
                        inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
        for row in db.execute("SELECT logo_path,qris_path,signature_path,stamp_path FROM business_profile"):
            references.extend(item for item in row if item)
        for table, columns in (("invoices", ("business_snapshot", "customer_snapshot")),
                               ("quotations", ("business_snapshot", "customer_snapshot")),
                               ("receipts", ("document_snapshot",))):
            for row in db.execute(f"SELECT {','.join(columns)} FROM {table}"):
                for raw in row:
                    inspect(json.loads(raw))
        return history[-1][0], references
