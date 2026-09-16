"""Atomic, checksum-verified SQLite migrations; no UI dependency."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from app.config.paths import default_database_path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a database. Caller owns and must close the returned connection."""
    connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Serialize writes and commit or roll back the entire operation."""
    if connection.in_transaction:
        raise RuntimeError("Nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _statements(script: str) -> Iterator[str]:
    # complete_statement handles semicolons inside trigger bodies and strings.
    pending = ""
    for character in script:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("Migration contains an incomplete SQL statement")


def migrate(connection: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> None:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.sql", path.name)
        if not match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        raw = path.read_bytes()
        migrations.append((int(match[1]), path.name, sha256(raw).hexdigest(),
                           raw.decode("utf-8-sig")))
    if not migrations or [m[0] for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("Migrations must be consecutive, starting with 001")

    with transaction(connection):
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ) STRICT
        """)
        applied = {row["version"]: row for row in
                   connection.execute("SELECT * FROM schema_migrations")}
        if sorted(applied) != list(range(1, len(applied) + 1)):
            raise ValueError("Database migration history is not consecutive")
        if len(applied) > len(migrations):
            raise ValueError("Database was created by a newer application version")
        for version, name, checksum, script in migrations:
            if version in applied:
                if (applied[version]["name"], applied[version]["checksum"]) != (name, checksum):
                    raise ValueError(f"Applied migration has changed: {name}")
                continue
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum) VALUES (?, ?, ?)",
                (version, name, checksum),
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Database contains invalid foreign keys")


def initialize_database(path: str | Path | None = None) -> Path:
    """Create an empty business database, or safely apply pending migrations."""
    destination = Path(path) if path is not None else default_database_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(destination)
    try:
        migrate(connection)
    finally:
        connection.close()
    return destination
