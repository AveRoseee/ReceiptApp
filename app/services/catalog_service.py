import sqlite3
from typing import Any, Mapping

from app.database import transaction
from app.repositories import catalog_repository as repository


MAX_INTEGER = 2**63 - 1


class CatalogValidationError(ValueError):
    """Data Katalog tidak memenuhi aturan."""


class CatalogItemNotFoundError(LookupError):
    """Item Katalog tidak ditemukan."""


def _clean_item(
    data: Mapping[str, Any]
) -> dict[str, Any]:
    unknown_fields = set(data) - set(repository.CATALOG_COLUMNS)

    if unknown_fields:
        raise CatalogValidationError(
            "Field katalog tidak sesuai: "
            + ", ".join(sorted(unknown_fields))
        )

    cleaned = {
        "sku": None,
        "type": "PRODUCT",
        "name": "",
        "description": "",
        "default_price": 0,
        "unit": "pcs",
    }
    cleaned.update(data)

    for field in ("type", "name", "description", "unit"):
        value = cleaned[field]

        if field == "description" and value is None:
            value = ""

        if not isinstance(value, str):
            raise CatalogValidationError(
                f"Nilai '{field}' harus berupa teks."
            )

        cleaned[field] = value.strip()

    cleaned["type"] = cleaned["type"].upper()

    if cleaned["type"] not in {"PRODUCT", "SERVICE"}:
        raise CatalogValidationError(
            "Tipe item harus PRODUCT atau SERVICE."
        )

    if not cleaned["name"]:
        raise CatalogValidationError(
            "Nama produk atau jasa wajib diisi."
        )

    if not cleaned["unit"]:
        raise CatalogValidationError(
            "Satuan wajib diisi."
        )

    price = cleaned["default_price"]

    if type(price) is not int or not 0 <= price <= MAX_INTEGER:
        raise CatalogValidationError(
            "Harga harus berupa bilangan bulat Rupiah "
            "dari 0 sampai batas integer database."
        )

    sku = cleaned["sku"]

    if sku is not None and not isinstance(sku, str):
        raise CatalogValidationError(
            "SKU harus berupa Teks."
        )

    cleaned["sku"] = (
        sku.strip().upper() or None
        if sku is not None
        else None
    )

    return cleaned


def _ensure_unique_sku(
    connection: sqlite3.Connection,
    sku: str | None,
    current_item_id: int | None = None,
) -> None:
    if sku is None:
        return

    existing = repository.get_item_by_sku(connection, sku)

    if existing is not None and existing["id"] != current_item_id:
        raise CatalogValidationError(
            f"SKU '{sku}' sudah digunakan oleh item lain." 
        )


def get_item(
    connection: sqlite3.Connection,
    item_id: int,
) -> dict[str, Any]:
    if (
        type(item_id) is not int
        or not 1 <= item_id <= MAX_INTEGER
    ):
        raise CatalogValidationError(
            "ID item tidak valid."
        )

    item = repository.get_item(connection, item_id)

    if item is None:
        raise CatalogItemNotFoundError(
            "Produk atau Jasa tidak Ditemukan"
        )

    return item

def list_items(
    connection: sqlite3.Connection,
    search: str = "",
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(search, str):
        raise CatalogValidationError(
            "Pencarian harus berupa Teks."
        )

    if type(include_archived) is not bool:
        raise CatalogValidationError(
            "Pilihan arsip harus berupa boolean."
        )

    if type(limit) is not int or not 1 <= limit <= 100:
        raise CatalogValidationError(
            "Jumlah item perhalaman harus 1 sampai 100."
        )

    if (
        type(offset) is not int
        or not 0 <= offset <= MAX_INTEGER
    ):
        raise CatalogValidationError(
            "Posisi awal daftar Katalog tidak valid"
        )

    return repository.list_items(
        connection,
        search=search.strip(),
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


def create_item(
    connection: sqlite3.Connection,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    cleaned = _clean_item(data)

    with transaction(connection):
        _ensure_unique_sku(
            connection,
            cleaned["sku"],
        )

        item_id = repository.create_item(
            connection,
            cleaned,
        )

        item = get_item(connection, item_id)

    return item


def update_item(
    connection: sqlite3.Connection,
    item_id: int,
    data: Mapping[str, Any]
) -> dict[str, Any]:
    with transaction(connection):
        current = get_item(connection, item_id)

        merged = {
            column: current[column]
            for column in repository.CATALOG_COLUMNS
        }
        merged.update(data)

        cleaned = _clean_item(merged)

        _ensure_unique_sku(
            connection,
            cleaned["sku"],
            current_item_id=item_id,
        )

        repository.update_item(
            connection,
            item_id,
            cleaned,
        )

        item = get_item(connection, item_id)

    return item


def set_item_active(
    connection: sqlite3.Connection,
    item_id: int,
    active: bool,
) -> dict[str, Any]:
    if type(active) is not bool:
        raise CatalogValidationError(
            "Status aktif harus berupa boolean."
        )

    with transaction(connection):
        get_item(connection, item_id)

        repository.set_item_active(
            connection,
            item_id,
            active,
        )

        item = get_item(connection, item_id)

    return item
