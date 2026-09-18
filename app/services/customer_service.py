import sqlite3
from typing import Any, Mapping

from app.database import transaction
from app.repositories import customer_repository as repository


class CustomerValidationError(ValueError):
    """Data Pelanggan tidak memenuhi aturan."""


class CustomerNotFoundError(LookupError):
    """Pelanggan yang diminta tidak ditemukan."""


def _clean_customer(
    data: Mapping[str, str | None],
) -> dict[str, str]:
    unknown_fields = set(data) - set(repository.CUSTOMER_COLUMNS)

    if unknown_fields:
        raise CustomerValidationError(
            "Field pelanggan tidak dikenal: "
            + ", ".join(sorted(unknown_fields))
        )

    cleaned = {
        column: ""
        for column in repository.CUSTOMER_COLUMNS
    }
    cleaned["type"] = "PERSONAL"

    for field, value in data.items():
        if value is not None and not isinstance(value, str):
            raise CustomerValidationError(
                f"Nilai '{field}' harus berupa teks."
            )

        cleaned[field] = value.strip() if value is not None else ""

    cleaned["type"] = cleaned["type"].upper()

    if cleaned["type"] not in {"PERSONAL", "COMPANY"}:
        raise CustomerValidationError(
            "Tipe pelanggan harus PERSONEL atau COMPANY."
        )

    if not cleaned["name"]:
        raise CustomerValidationError(
            "Nama pelanggan wajib diisi."
        )

    return cleaned


def get_customer(
    connection: sqlite3.Connection,
    customer_id: int,
) -> dict[str, Any]:
    if (
        type(customer_id) is not int
        or not 1 <= customer_id <= 2**63 - 1
    ):
        raise CustomerValidationError(
            "ID pelanggan tidak valid"
        )

    customer = repository.get_customer(
        connection,
        customer_id,
    )

    if customer is None:
        raise CustomerNotFoundError(
            "Pelanggan tidak ditemukan."
        )

    return customer


def list_customers(
    connection: sqlite3.Connection,
    search: str = "",
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(search, str):
        raise CustomerValidationError(
            "Pencarian harus berupa teks."
        )

    if type(include_archived) is not bool:
        raise CustomerValidationError(
            "Jumlah pelanggan per halaman harus 1 sampai 100"
        )

    if (
        type(offset) is not int
        or not 0 <= offset <= 2**63 - 1
    ):
        raise CustomerValidationError(
            "Posisi awal daftar tidak valid."
        )

    return repository.list_customers(
        connection,
        search=search.strip(),
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


def create_customer(
    connection: sqlite3.Connection,
    data: Mapping[str, str | None],
) -> dict[str, Any]:
    cleaned = _clean_customer(data)

    with transaction(connection):
        customer_id = repository.create_customer(
            connection,
            cleaned,
        )

        customer = get_customer(connection, customer_id)

    return customer


def update_customer(
    connection: sqlite3.Connection,
    customer_id: int,
    data: Mapping[str, str | None],
) -> dict[str, Any]:
    with transaction(connection):
        current = get_customer(connection, customer_id)

        merged = {
            column: current[column]
            for column in repository.CUSTOMER_COLUMNS
        }
        merged.update(data)

        cleaned = _clean_customer(merged)

        repository.update_customer(
            connection,
            customer_id,
            cleaned,
        )

        customer = get_customer(connection, customer_id)

    return customer


def set_customer_active(
    connection: sqlite3.Connection,
    customer_id: int,
    active: bool,
) -> dict[str, Any]:
    if type(active) is not bool:
        raise CustomerValidationError(
            "Status aktif harus berupa boolean"
        )

    with transaction(connection):
        get_customer(connection, customer_id)

        repository.set_customer_active(
            connection,
            customer_id,
            active,
        )

        customer = get_customer(connection, customer_id)

    return customer