import pytest

from app.database import connect, initialize_database
from app.services.customer_service import (
    CustomerNotFoundError,
    CustomerValidationError,
    create_customer,
    get_customer,
    list_customers,
    set_customer_active,
    update_customer,
)


@pytest.fixture
def connection(tmp_path):
    database_path = initialize_database(
        tmp_path / "customers.db"
    )

    database_connection = connect(database_path)

    try:
        yield database_connection
    finally:
        database_connection.close()


def test_create_personal_customer(connection):
    customer = create_customer(
        connection,
        {
            "name": "  Budi  ",
            "phone": "  081234567890  ",
        },
    )

    assert customer["name"] == "Budi"
    assert customer["phone"] == "081234567890"
    assert customer["type"] == "PERSONAL"
    assert customer["is_active"] == 1


def test_create_company_customer(connection):
    customer = create_customer(
        connection,
        {
            "type": "company",
            "name": "Andi",
            "company_name": "PT Contoh",
        },
    )

    assert customer["type"] == "COMPANY"
    assert customer["company_name"] == "PT Contoh"


@pytest.mark.parametrize(
    "data",
    [
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"name": "Budi", "type": "OTHER"},
        {"name": "Budi", "phone": 81234567890},
        {"name": "Budi", "is_active": "0"},
    ],
)
def test_invalid_customer_is_rejected(connection, data):
    with pytest.raises(CustomerValidationError):
        create_customer(connection, data)

    assert list_customers(
        connection,
        include_archived=True,
    ) == []


def test_partial_update_preserves_other_fields(connection):
    customer = create_customer(
        connection,
        {
            "name": "Budi",
            "address": "Bandung",
            "phone": "081234567890",
        },
    )

    updated = update_customer(
        connection,
        customer["id"],
        {"name": "Budi Santoso"},
    )

    assert updated["name"] == "Budi Santoso"
    assert updated["address"] == "Bandung"
    assert updated["phone"] == "081234567890"


def test_invalid_update_preserves_previous_data(connection):
    customer = create_customer(
        connection,
        {
            "name": "Budi",
            "address": "Bandung",
        },
    )

    with pytest.raises(CustomerValidationError):
        update_customer(
            connection,
            customer["id"],
            {
                "name": "",
                "address": "Jakarta",
            },
        )

    saved = get_customer(connection, customer["id"])

    assert saved["name"] == "Budi"
    assert saved["address"] == "Bandung"
    assert not connection.in_transaction


def test_search_by_company_name(connection):
    target = create_customer(
        connection,
        {
            "name": "Andi",
            "type": "COMPANY",
            "company_name": "Bandung Creative",
        },
    )

    create_customer(
        connection,
        {"name": "Budi"},
    )

    results = list_customers(
        connection,
        search="creative",
    )

    assert [item["id"] for item in results] == [target["id"]]


def test_archive_preserves_customer_and_document(connection):
    customer = create_customer(
        connection,
        {"name": "Budi"},
    )

    quotation_id = connection.execute(
        """
        INSERT INTO quotations(customer_id, issue_date)
        VALUES (?, ?)
        """,
        (customer["id"], "2026-09-17"),
    ).lastrowid

    archived = set_customer_active(
        connection,
        customer["id"],
        False,
    )

    assert archived["is_active"] == 0
    assert list_customers(connection) == []

    all_customers = list_customers(
        connection,
        include_archived=True,
    )

    assert len(all_customers) == 1

    quotation = connection.execute(
        "SELECT customer_id FROM quotations WHERE id = ?",
        (quotation_id,),
    ).fetchone()

    assert quotation["customer_id"] == customer["id"]

    restored = set_customer_active(
        connection,
        customer["id"],
        True,
    )

    assert restored["is_active"] == 1
    assert len(list_customers(connection)) == 1


def test_missing_customer_is_reported(connection):
    with pytest.raises(CustomerNotFoundError):
        update_customer(
            connection,
            999,
            {"name": "Tidak Ada"},
        )

    assert list_customers(connection) == []