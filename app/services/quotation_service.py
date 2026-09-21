from contextlib import closing, ExitStack
import json
from pathlib import Path

from datetime import date
from typing import Mapping

from app.database import transaction
from app.repositories import catalog_repository, customer_repository
from app.repositories import quotation_repository as repository
from app.services.document_calculator import (
    DocumentCalculationError,
    calculate_document,
)
from app.database import connect
from app.services.business_profile_service import (
    TEXT_FIELDS,
    get_business_profile,
)
from app.services.document_asset_service import (
    snapshot_business_images,
)
from app.services.document_number_service import (
    allocate_document_number,
)


MAX_INTEGER = 2**63 - 1

HEADER_INPUTS = {
    "customer_id",
    "issue_date",
    "valid_until",
    "discount_type",
    "discount_value",
    "tax_rate_bps",
    "notes",
    "terms",
}

ITEM_INPUTS = {
    "catalog_item_id",
    "name_snapshot",
    "description",
    "quantity_milli",
    "unit",
    "unit_price",
}

QUOTATION_STATUSES = {
    "DRAFT",
    "SENT",
    "ACCEPTED",
    "REJECTED",
    "EXPIRED",
    "CONVERTED",
}

MANUAL_TARGET_STATUSES = {
    "ACCEPTED",
    "REJECTED",
    "EXPIRED",
}


class QuotationValidationError(ValueError):
    """Input Penawaran tidak valid."""


class QuotationNotFoundError(LookupError):
    """Status penawaran tidak dapat ditemukan."""

class QuotationStateError(ValueError):
    """Status penawaran tidak mengizinkan operasi ini"""


def _integer(value, label, minimum = 1):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise QuotationValidationError(
            f"{label} harus berupa teks."
        )

    return value


def _text(value, label, required = False):
    if not isinstance(value, str):
        raise QuotationValidationError(
            f"{label} harus berupa teks."
        )

    value = value.strip()

    if required and not value:
        raise QuotationValidationError(
            f"{label} wajib diisi."
        )

    return value


def _date(value, label):
    if not isinstance(value, str):
        raise QuotationValidationError(
            f"{label} harus dalam format YYYY-MM-DD."
        )

    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise QuotationValidationError(
            f"{label} tidak valid"
        ) from error

    if parsed.isoformat() != value:
        raise QuotationValidationError(
            f"{label} harus dalam format YYYY-MM-DD."
        )

    return value


def _check_fields(data, allowed):
    if not isinstance(data, Mapping) or set(data) - allowed:
        raise QuotationValidationError(
            "Struktur atau Field Input tidak sesuai."
        )


def _prepare_item(connection, raw, position):
    _check_fields(raw, ITEM_INPUTS)

    item = {
        "catalog_item_id": None,
        "name_snapshot": "",
        "description": "",
        "unit": "",
        "unit_price": None,
        "quantity_milli": None,
    }

    catalog_id = raw.get("catalog_item_id")

    if catalog_id is not None:
        _integer(catalog_id, "ID katalog")
        catalog = catalog_repository.get_item(
            connection,
            catalog_id,
        )

        if catalog is None or not catalog["is_active"]:
            raise QuotationValidationError(
                "Item katalog harus ada dan aktif."
            )

        item.update(
            catalog_item_id=catalog_id,
            name_snapshot=catalog["name"],
            description=catalog["description"],
            unit=catalog["unit"],
            unit_price=catalog["default_price"],
        )

    item.update(raw)

    for field in ("name_snapshot", "description", "unit"):
        item[field] = _text(
            item[field],
            field,
            required = field != "description",
        )

    item["quantity_milli"] = _integer(
        item["quantity_milli"],
        "Kuantitas",
    )

    item["unit_price"] = _integer(
        item["unit_price"],
        "Harga Satuan",
        minimum = 0,
    )
    item["position"] = position

    return item


def _prepare(connection, data):
    _check_fields(data, HEADER_INPUTS | {"items"})

    header = {
        "customer_id": None,
        "issue_date": None, 
        "valid_until": None,
        "discount_type": "AMOUNT",
        "discount_value": 0,
        "tax_rate_bps": 0,
        "notes": "",
        "terms": "",
    }
    header.update(
        {
            key: value
            for key, value in data.items()
            if key != "items"
        }
    )

    _integer(header["customer_id"], "ID Pelanggan")

    customer = customer_repository.get_customer(
        connection,
        header["customer_id"],
    )

    if customer is None or not customer["is_active"]:
            raise QuotationValidationError(
                "Pelanggan harus ADA atau AKTIF."
            )

    header["issue_date"] = _date(
        header["issue_date"],
        "Tanggal Penawaran"
    )

    if header["valid_until"] is not None:
        header["valid_until"] = _date(
            header["valid_until"],
            "Tanggal Berlaku",
        )

        if header["valid_until"] < header["issue_date"]:
            raise QuotationValidationError(
                "Tanggal berlaku tidak boleh "
                "mendahului tanggal penawaran."
            )

    for field in ("notes", "terms"):
        header[field] = _text(header[field], field)

    raw_items = data.get("items", [])

    if not isinstance(raw_items, list):
        raise QuotationValidationError(
            "Items harus berupa list."
        )

    items = [
        _prepare_item(connection, item, position)
        for position, item in enumerate(raw_items, start = 1)
    ]

    try:
        totals = calculate_document(
            items,
            discount_type = header["discount_type"],
            discount_value = header["discount_value"],
            tax_rate_bps = header["tax_rate_bps"],
        )
    except DocumentCalculationError as error:
        raise QuotationValidationError(
            str(error)
        ) from error

    for item, total in zip(items, totals.pop("line_totals")):
        item["line_total"] = total

    header.update(totals)

    return header, items


def get_quotation(connection, quotation_id: int) -> dict:
    _integer(quotation_id, "ID Penawaran")

    result = repository.get_quotation(
        connection,
        quotation_id,
    )

    if result is None:
        raise QuotationNotFoundError(
            "Penawaran tidak ditemukan"
        )

    return result


def create_draft(connection, data: Mapping) -> dict:
    with transaction(connection):
        header, items = _prepare(connection, data)

        quotation_id = repository.insert_draft(
            connection,
            header,
        )

        repository.replace_items(
            connection,
            quotation_id,
            items,
        )

        repository.record_event(
            connection,
            quotation_id,
            "DRAFT_CREATED",
        )

        result = get_quotation(connection, quotation_id)

    return result


def update_draft(
    connection,
    quotation_id: int,
    data: Mapping,
) -> dict:
    _check_fields(data, HEADER_INPUTS | {"items"})

    with transaction(connection):
        current = get_quotation(connection, quotation_id)

        if current["status"] != "DRAFT" or current["number"] is not None:
            raise QuotationStateError(
                "Hanya DRAFT tanpa nomor yang dapat di edit."
            )

        merged = {
            key: current[key]
            for key in HEADER_INPUTS
        }
        merged["items"] = [
            {
                key: item[key]
                for key in ITEM_INPUTS
            }
            for item in current["items"]
        ]
        merged.update(data)

        header, items = _prepare(connection, merged)

        repository.update_draft(
            connection,
            quotation_id,
            header,
        )
        repository.replace_items(
            connection,
            quotation_id,
            items,
        )
        repository.record_event(
            connection,
            quotation_id,
            "DRAFT UPDATED"
        )

        result = get_quotation(connection, quotation_id)

    return result


def publish_quotation(
    database_path: str | None,
    quotation_id: int,
) -> dict:
    database_path = Path(database_path).resolve()

    if not database_path.is_file():
        raise QuotationValidationError(
            "File database tidak ditemukan."
        )

    with closing(connect(database_path)) as connection:
        with ExitStack() as assets:
            with transaction(connection):
                current = get_quotation(
                    connection,
                    quotation_id,
                )

                if (
                    current["status"] != "DRAFT"
                    or current["number"] is not None
                ):
                    raise QuotationStateError(
                        "Hanya draft tanpa nomor "
                        "yang dapat diterbitkan."
                    )

                if not current["items"]:
                    raise QuotationValidationError(
                        "Penawaran harus memiliki minimal satu item."
                    )

                profile = get_business_profile(connection)

                if profile is None:
                    raise QuotationValidationError(
                        "Simpan profil usaha sebelum "
                        "menerbitkan penawaran."
                    )

                _text(
                    profile["name"],
                    "Nama usaha",
                    required = True
                )

                data = {
                    key: current[key]
                    for key in HEADER_INPUTS
                }
                data["items"] = [
                    {
                        key: item[key]
                        for key in ITEM_INPUTS
                    }
                    for item in current["items"]
                ]

                header, items = _prepare(connection, data)

                customer = customer_repository.get_customer(
                    connection,
                    header["customer_id"],
                )

                image_paths = assets.enter_context(
                    snapshot_business_images(
                        database_path,
                        profile,
                    )
                )

                business_data = {
                    key: profile[key]
                    for key in TEXT_FIELDS
                }
                business_data.update(image_paths)

                customer_data = {
                    key: customer[key]
                    for key in (
                        "id",
                        *customer_repository.CUSTOMER_COLUMNS,
                    )
                }

                business_snapshot = json.dumps(
                    {
                        "schema_version": 1,
                        "data": business_data,
                    },
                    ensure_ascii = False,
                    sort_keys = True,
                )
                customer_snapshot = json.dumps(
                    {
                        "schema_version": 1,
                        "data": customer_data,
                    },
                    ensure_ascii = True,
                    sort_keys = True,
                )
                
                repository.update_draft(
                    connection,
                    quotation_id,
                    header,
                )
                repository.replace_items(
                    connection,
                    quotation_id,
                    items,
                )

                number = allocate_document_number(
                    connection,
                    "QUOTATION",
                    header["issue_date"],
                )

                repository.mark_sent(
                    connection,
                    quotation_id,
                    number,
                    business_snapshot,
                    customer_snapshot,
                )
                repository.record_event(
                    connection,
                    quotation_id,
                    "QUOTATION_SENT",
                )

                result = get_quotation(
                    connection,
                    quotation_id
                )

    return result


def change_quotation_status(
    connection,
    quotation_id: int,
    new_status: str, 
    *,
    today: date | None = None 
) -> dict:
    new_status = _text(
        new_status,
        "Status",
        required = True,
    ).upper()

    if new_status not in MANUAL_TARGET_STATUSES:
        raise QuotationValidationError(
            "Status tujuan harus ACCEPTED, REJECTED, atau EXPIRED"
        )

    with transaction(connection):
        current = get_quotation(
            connection,
            quotation_id,
        )

        if current["status"] != "SENT":
            raise QuotationStateError(
                "Hanya penawaran berstatus SENT "
                "yang dapat di proses."
            )

        valid_until = current["valid_until"]

        if valid_until is not None:
            valid_until = _date(
                valid_until,
                "Tanggal berlaku",
            )

        overdue = (
            valid_until is not None
            and valid_until < today.isoformat()
        )

        if new_status == "EXPIRED" and not overdue:
            raise QuotationStateError(
                "Penawaran belum melewati batas berlaku "
                "atau tidak memiliki batas berlaku."
            )

        if new_status == "ACCEPTED" and overdue:
            raise QuotationStateError(
                "Penawaran sudah melewati batas berlaku. "
                "Tandai EXPIRED, lalu membuat penawaran baru "
                "jika diperlukan."
            )

        repository.change_sent_status(
            connection,
            quotation_id,
            new_status,
        )
        repository.record_event(
            connection,
            quotation_id,
            f"QUOTATION_{new_status}"
        )

        result = get_quotation(
            connection,
            quotation_id,
        )

    return result


def list_quotations(
    connection,
    search: str = "",
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    search = _text(search, "Pencarian")

    if status is not None:
        status = _text(
            status,
            "Status",
            required = True,
        ).upper()

        if status not in QUOTATION_STATUSES:
            raise QuotationValidationError(
                "Filter status tidak dikenal."
            )

    if type(limit) is not int or not 1 <= limit <= 100:
        raise QuotationValidationError(
            "Limit harus berupa integer 1 sampai 100"
        )

    _integer(offset, "Offset", minimum = 0)

    return repository.list_quotations(
        connection,
        search = search,
        status = status,
        limit = limit,
        offset = offset,
    )