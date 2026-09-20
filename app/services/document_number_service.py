from datetime import date
import re
import sqlite3

from app.repositories import document_number_repository as repository


MAX_INTEGER = 2**63 - 1

DEFAULT_FORMAT = "{prefix}-{year}-{seq:04d}"

DOCUMENT_TYPES = {
    "QUOTATION",
    "INVOICE",
    "RECEIPT",
}

class DocumentNumberError(ValueError):
    """Input atau Konfigurasi nomor dokumen tidak valid."""


def allocate_document_number(
    connection: sqlite3.Connection,
    document_type: str,
    issue_date: str,
) -> str:
    if not connection.in_transaction:
        raise RuntimeError(
            "Alokasi Nomor harus berada dalam transaksi penerbitan."
        )

    if not isinstance(document_type, str):
        raise DocumentNumberError(
            "Jenis dokumen harus berupa teks."
        )

    document_type = document_type.strip().upper()

    if document_type not in DOCUMENT_TYPES:
        raise DocumentNumberError(
            "Jenis Dokumen tidak dikenal."
        )

    if not isinstance(issue_date, str):
        raise DocumentNumberError(
            "Tanggal dokumen tidak valid."
        )

    try:
        parsed_date = date.fromisoformat(issue_date)
    except ValueError as error:
        raise DocumentNumberError(
            "Tanggal dokumen tidak valid"
        ) from error

    if parsed_date.isoformat() != issue_date:
        raise DocumentNumberError(
            "Tanggal harus berupa Format 'YYYY-MM-DD.'"
        )

    sequence = repository.get_sequence(
        connection,
        document_type,
    )

    if sequence is None:
        raise DocumentNumberError(
            "Konfigurasi nomor tidak ditemukan."
        )

    prefix = sequence["prefix"]

    if not isinstance(prefix, str) or re.fullmatch(
        r"[A-Za-z0-9_-]{1,20}", prefix
    ) is None:
        raise DocumentNumberError(
            "Prefix harus 1-20 Karakter: "
            "huruf, angka, garis bawah, atau strip."
        )

    if sequence["number_format"] != DEFAULT_FORMAT:
        raise DocumentNumberError(
            "Format nomor belum didukung. Gunakan "
            + DEFAULT_FORMAT
        )

    last_value = sequence["last_value"]

    if (
        type(last_value) is not int
        or not 0 <= last_value < MAX_INTEGER
    ):
        raise DocumentNumberError(
            "Counter tidak valid atau sudah mencapai batas."
        )

    number = DEFAULT_FORMAT.format(
        prefix = prefix,
        year = f"{parsed_date.year:04d}",
        seq = last_value + 1,
    )

    repository.advance_sequence(
        connection,
        document_type,
        last_value,
    )

    return number

