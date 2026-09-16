"""Resolve writable user data without creating directories during import."""

from pathlib import Path

from platformdirs import user_data_path


def default_database_path() -> Path:
    return user_data_path("DokumenUsaha", appauthor=False) / "dokumenusaha.db"
