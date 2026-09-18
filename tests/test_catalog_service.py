import pytest

from app.database import connect, initialize_database
from app.services.catalog_service import (
    CatalogItemNotFoundError,
    CatalogValidationError,
    create_item,
    get_item,
    list_items,
    set_item_active,
    update_item,
)


@pytest.fixture
def connection(tmp_path):
    database_path = initialize_database(
        tmp_path / "catalog.db"
    )

    database_connection = connect(database_path)

    try:
        yield database_connection
    finally:
        database_connection.close()


def test_create_product(connection):
    item = create_item(
        connection,
        {
            "sku": "  prd-001  ",
            "name": "  Kertas A4  ",
            "default_price": 55000,
            "unit": "rim",
        },
    )

    assert item["sku"] == "PRD-001"
    assert item["name"] == "Kertas A4"
    assert item["type"] == "PRODUCT"
    assert item["default_price"] == 55000
    assert item["is_active"] == 1


def test_create_service(connection):
    item = create_item(
        connection,
        {
            "type": "service",
            "name": "Desain Logo",
            "default_price": 500000,
            "unit": "project",
        },
    )

    assert item["type"] == "SERVICE"
    assert item["unit"] == "project"
    assert item["sku"] is None


@pytest.mark.parametrize(
    "price",
    [-1, 1.5, "150000", True, None, 2**63],
)
def test_invalid_price_is_rejected(connection, price):
    with pytest.raises(CatalogValidationError):
        create_item(
            connection,
            {
                "name": "Contoh",
                "default_price": price,
            },
        )

    assert list_items(connection) == []


@pytest.mark.parametrize(
    "data",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "Contoh", "unit": ""},
        {"name": "Contoh", "type": "OTHER"},
        {"name": "Contoh", "sku": 123},
        {"name": "Contoh", "is_active": 0},
    ],
)
def test_invalid_fields_are_rejected(connection, data):
    with pytest.raises(CatalogValidationError):
        create_item(connection, data)

    assert list_items(connection) == []


def test_zero_price_is_allowed(connection):
    item = create_item(
        connection,
        {
            "name": "Konsultasi Awal",
            "type": "SERVICE",
            "default_price": 0,
            "unit": "sesi",
        },
    )

    assert item["default_price"] == 0


def test_multiple_items_without_sku_are_allowed(connection):
    first = create_item(
        connection,
        {"name": "Item A", "sku": ""},
    )

    second = create_item(
        connection,
        {"name": "Item B", "sku": None},
    )

    assert first["sku"] is None
    assert second["sku"] is None
    assert first["id"] != second["id"]


def test_duplicate_sku_is_rejected_after_archiving(connection):
    original = create_item(
        connection,
        {"name": "Item A", "sku": "SKU-001"},
    )

    set_item_active(connection, original["id"], False)

    with pytest.raises(CatalogValidationError, match="SKU"):
        create_item(
            connection,
            {"name": "Item B", "sku": " sku-001 "},
        )

    assert len(
        list_items(connection, include_archived=True)
    ) == 1


def test_partial_update_preserves_other_fields(connection):
    item = create_item(
        connection,
        {
            "name": "Desain Logo",
            "sku": "DSN-001",
            "type": "SERVICE",
            "default_price": 500000,
            "unit": "project",
        },
    )

    updated = update_item(
        connection,
        item["id"],
        {"default_price": 750000},
    )

    assert updated["default_price"] == 750000
    assert updated["name"] == "Desain Logo"
    assert updated["sku"] == "DSN-001"
    assert updated["unit"] == "project"


def test_duplicate_sku_update_rolls_back(connection):
    first = create_item(
        connection,
        {"name": "Item A", "sku": "A"},
    )

    second = create_item(
        connection,
        {"name": "Item B", "sku": "B"},
    )

    with pytest.raises(CatalogValidationError):
        update_item(
            connection,
            second["id"],
            {
                "name": "Nama Berubah",
                "sku": first["sku"],
            },
        )

    saved = get_item(connection, second["id"])

    assert saved["name"] == "Item B"
    assert saved["sku"] == "B"
    assert not connection.in_transaction


def test_search_by_sku(connection):
    target = create_item(
        connection,
        {"name": "Desain Logo", "sku": "DSN-001"},
    )

    create_item(connection, {"name": "Kertas A4"})

    results = list_items(connection, search="dsn-001")

    assert [item["id"] for item in results] == [target["id"]]


def test_archive_and_restore(connection):
    item = create_item(
        connection,
        {"name": "Item Lama"},
    )

    set_item_active(connection, item["id"], False)

    assert list_items(connection) == []
    assert get_item(connection, item["id"])["is_active"] == 0
    assert len(
        list_items(connection, include_archived=True)
    ) == 1

    set_item_active(connection, item["id"], True)

    assert len(list_items(connection)) == 1


def test_missing_item_is_reported(connection):
    with pytest.raises(CatalogItemNotFoundError):
        update_item(
            connection,
            999,
            {"name": "Tidak Ada"},
        )


@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_page_limit_is_rejected(connection, limit):
    with pytest.raises(CatalogValidationError):
        list_items(connection, limit=limit)