import pytest

from app.database import connect, initialize_database, transaction
from app.repositories.document_number_repository import get_sequence
from app.services.document_number_service import (
    MAX_INTEGER,
    DocumentNumberError,
    allocate_document_number,
)


@pytest.fixture
def connection(tmp_path):
    database_path = initialize_database(
        tmp_path / "numbers.db"
    )
    database_connection = connect(database_path)

    try:
        yield database_connection
    finally:
        database_connection.close()


def test_numbers_increase(connection):
    with transaction(connection):
        first = allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )

    with transaction(connection):
        second = allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )

    assert first == "INV-2026-0001"
    assert second == "INV-2026-0002"
    assert get_sequence(connection, "INVOICE")["last_value"] == 2


def test_document_types_have_independent_counters(connection):
    with transaction(connection):
        quotation = allocate_document_number(
            connection, "QUOTATION", "2026-09-20"
        )
        invoice = allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )
        receipt = allocate_document_number(
            connection, "RECEIPT", "2026-09-20"
        )

    assert quotation == "QUO-2026-0001"
    assert invoice == "INV-2026-0001"
    assert receipt == "RCPT-2026-0001"


def test_new_year_does_not_reset_counter(connection):
    with transaction(connection):
        first = allocate_document_number(
            connection, "INVOICE", "2026-12-31"
        )
        second = allocate_document_number(
            connection, "INVOICE", "2027-01-01"
        )

    assert first == "INV-2026-0001"
    assert second == "INV-2027-0002"


def test_allocation_requires_transaction(connection):
    with pytest.raises(RuntimeError, match="transaksi"):
        allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )

    assert get_sequence(connection, "INVOICE")["last_value"] == 0


def test_document_and_counter_roll_back_together(connection):
    connection.execute(
        "INSERT INTO customers(id, name) VALUES (?, ?)",
        (1, "Pelanggan Contoh"),
    )

    with pytest.raises(RuntimeError, match="Simulasi"):
        with transaction(connection):
            number = allocate_document_number(
                connection, "INVOICE", "2026-09-20"
            )

            connection.execute(
                """
                INSERT INTO invoices(
                    customer_id, number, issue_date
                )
                VALUES (?, ?, ?)
                """,
                (1, number, "2026-09-20"),
            )

            assert connection.in_transaction
            raise RuntimeError("Simulasi penyimpanan gagal")

    assert get_sequence(connection, "INVOICE")["last_value"] == 0

    invoice_count = connection.execute(
        "SELECT COUNT(*) FROM invoices"
    ).fetchone()[0]

    assert invoice_count == 0

    with transaction(connection):
        next_number = allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )

    assert next_number == "INV-2026-0001"


@pytest.mark.parametrize(
    "document_type",
    ["OTHER", "", None, True, 123],
)
def test_invalid_document_type_is_rejected(
    connection,
    document_type,
):
    with pytest.raises(DocumentNumberError):
        with transaction(connection):
            allocate_document_number(
                connection, document_type, "2026-09-20"
            )

    assert get_sequence(connection, "INVOICE")["last_value"] == 0


@pytest.mark.parametrize(
    "issue_date",
    [
        "2026-02-30",
        "2026-13-01",
        "20260920",
        "2026-9-20",
        "20-09-2026",
        "",
        None,
        True,
    ],
)
def test_invalid_date_is_rejected(connection, issue_date):
    with pytest.raises(DocumentNumberError):
        with transaction(connection):
            allocate_document_number(
                connection, "INVOICE", issue_date
            )

    assert get_sequence(connection, "INVOICE")["last_value"] == 0


def test_document_type_is_normalized(connection):
    with transaction(connection):
        number = allocate_document_number(
            connection, " invoice ", "2026-09-20"
        )

    assert number == "INV-2026-0001"


def test_counter_can_exceed_four_digits(connection):
    with transaction(connection):
        connection.execute(
            """
            UPDATE document_sequences
            SET last_value = 9999
            WHERE document_type = 'INVOICE'
            """
        )

        number = allocate_document_number(
            connection, "INVOICE", "2026-09-20"
        )

    assert number == "INV-2026-10000"


def test_exhausted_counter_is_rejected(connection):
    with transaction(connection):
        connection.execute(
            """
            UPDATE document_sequences
            SET last_value = ?
            WHERE document_type = 'INVOICE'
            """,
            (MAX_INTEGER,),
        )

    with pytest.raises(DocumentNumberError):
        with transaction(connection):
            allocate_document_number(
                connection, "INVOICE", "2026-09-20"
            )

    assert (
        get_sequence(connection, "INVOICE")["last_value"]
        == MAX_INTEGER
    )


def test_unsupported_format_does_not_advance_counter(connection):
    with transaction(connection):
        connection.execute(
            """
            UPDATE document_sequences
            SET number_format = ?
            WHERE document_type = 'INVOICE'
            """,
            ("{prefix}/{seq}",),
        )

    with pytest.raises(DocumentNumberError, match="Format"):
        with transaction(connection):
            allocate_document_number(
                connection, "INVOICE", "2026-09-20"
            )

    assert get_sequence(connection, "INVOICE")["last_value"] == 0


def test_invalid_prefix_does_not_advance_counter(connection):
    with transaction(connection):
        connection.execute(
            """
            UPDATE document_sequences
            SET prefix = ?
            WHERE document_type = 'INVOICE'
            """,
            ("INV TEST",),
        )

    with pytest.raises(DocumentNumberError, match="Prefix"):
        with transaction(connection):
            allocate_document_number(
                connection, "INVOICE", "2026-09-20"
            )

    assert get_sequence(connection, "INVOICE")["last_value"] == 0

    