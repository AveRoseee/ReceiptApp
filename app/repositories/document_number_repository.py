import sqlite3
from typing import Any


def get_sequence(
    connection:sqlite3.Connection,
    document_type: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT document_type, prefix, number_format, last_value
        FROM document_sequences
        WHERE document_type = ?
        """,
        (document_type,),
    ).fetchone()

    return dict(row) if row is not None else None


def advance_sequence(
    connection: sqlite3.Connection,
    document_type: str,
    expected_value: int,
) -> None:
    cursor = connection.execute(
        """
        UPDATE document_sequences
        SET last_value = ?
        WHERE document_type = ? AND last_value = ?
        """,
        (expected_value + 1, document_type, expected_value)
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Counter Dokumen berubah atau tidak ditemukan."
        )