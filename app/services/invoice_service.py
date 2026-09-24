"""Validation and atomic operations for invoices created without quotations."""
from contextlib import ExitStack, closing
from datetime import date
import json
from pathlib import Path
from typing import Mapping

from app.database import transaction, connect
from app.repositories import catalog_repository, customer_repository
from app.repositories import invoice_repository as repository

from app.services.document_calculator import (
    DocumentCalculationError,
    calculate_document,
)
from app.services.business_profile_service import (
    TEXT_FIELDS,
    get_business_profile,
)
from app.services.document_asset_service import snapshot_business_images
from app.services.document_number_service import allocate_document_number

MAX_INTEGER = 2**63 - 1
HEADER_INPUTS = {
    "customer_id", "issue_date", "due_date", "discount_type",
    "discount_value", "tax_rate_bps", "notes", "terms",
}
ITEM_INPUTS = {
    "catalog_item_id", "name_snapshot", "description",
    "quantity_milli", "unit", "unit_price",
}
INVOICE_STATUSES = {"DRAFT", "ISSUED", "CANCELLED"}


class InvoiceValidationError(ValueError):
    """Invoice input is invalid."""


class InvoiceNotFoundError(LookupError):
    """Requested invoice does not exist."""


class InvoiceStateError(ValueError):
    """Invoice state does not allow this operation."""


def _integer(value, label, minimum=1):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise InvoiceValidationError(
            f"{label} harus berupa bilangan bulat antara {minimum} dan {MAX_INTEGER}."
        )
    return value


def _text(value, label, required=False):
    if not isinstance(value, str):
        raise InvoiceValidationError(f"{label} harus berupa teks.")
    value = value.strip()
    if required and not value:
        raise InvoiceValidationError(f"{label} wajib diisi.")
    return value


def _date(value, label):
    if not isinstance(value, str):
        raise InvoiceValidationError(f"{label} harus dalam format YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InvoiceValidationError(f"{label} tidak valid.") from error
    if parsed.isoformat() != value:
        raise InvoiceValidationError(f"{label} harus dalam format YYYY-MM-DD.")
    return value


def _check_fields(data, allowed):
    if not isinstance(data, Mapping) or set(data) - allowed:
        raise InvoiceValidationError("Struktur atau field input tidak sesuai.")


def _prepare_item(connection, raw, position):
    _check_fields(raw, ITEM_INPUTS)
    item = {
        "catalog_item_id": None, "name_snapshot": "", "description": "",
        "unit": "", "unit_price": None, "quantity_milli": None,
    }
    catalog_id = raw.get("catalog_item_id")
    if catalog_id is not None:
        _integer(catalog_id, "ID katalog")
        catalog = catalog_repository.get_item(connection, catalog_id)
        if catalog is None or not catalog["is_active"]:
            raise InvoiceValidationError("Item katalog harus ada dan aktif.")
        item.update(
            catalog_item_id=catalog_id,
            name_snapshot=catalog["name"],
            description=catalog["description"],
            unit=catalog["unit"],
            unit_price=catalog["default_price"],
        )

    item.update(raw)
    labels = {
        "name_snapshot": "Nama item", "description": "Deskripsi", "unit": "Satuan",
    }
    for key, label in labels.items():
        item[key] = _text(item[key], label, required=key != "description")
    item["quantity_milli"] = _integer(item["quantity_milli"], "Kuantitas")
    item["unit_price"] = _integer(item["unit_price"], "Harga satuan", minimum=0)
    item["position"] = position
    return item


def _prepare(connection, data):
    _check_fields(data, HEADER_INPUTS | {"items"})
    header = {
        "customer_id": None, "issue_date": None, "due_date": None,
        "discount_type": "AMOUNT", "discount_value": 0, "tax_rate_bps": 0,
        "notes": "", "terms": "",
    }
    header.update({key: value for key, value in data.items() if key != "items"})
    _integer(header["customer_id"], "ID pelanggan")
    customer = customer_repository.get_customer(connection, header["customer_id"])
    if customer is None or not customer["is_active"]:
        raise InvoiceValidationError("Pelanggan harus ada dan aktif.")
    header["issue_date"] = _date(header["issue_date"], "Tanggal invoice")
    if header["due_date"] is not None:
        header["due_date"] = _date(header["due_date"], "Tanggal jatuh tempo")
        if header["due_date"] < header["issue_date"]:
            raise InvoiceValidationError(
                "Tanggal jatuh tempo tidak boleh mendahului tanggal invoice."
            )
    for key in ("notes", "terms"):
        header[key] = _text(header[key], "Catatan" if key == "notes" else "Syarat")
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise InvoiceValidationError("Items harus berupa list.")
    items = [
        _prepare_item(connection, raw, position)
        for position, raw in enumerate(raw_items, 1)
    ]
    try:
        totals = calculate_document(
            items, discount_type=header["discount_type"],
            discount_value=header["discount_value"],
            tax_rate_bps=header["tax_rate_bps"],
        )
    except DocumentCalculationError as error:
        raise InvoiceValidationError(str(error)) from error

    for item, total in zip(items, totals.pop("line_totals")):
        item["line_total"] = total
    header.update(totals)
    return header, items


def get_invoice(connection, invoice_id: int) -> dict:
    _integer(invoice_id, "ID invoice")
    result = repository.get_invoice(connection, invoice_id)
    if result is None:
        raise InvoiceNotFoundError("Invoice tidak ditemukan.")
    return result


def create_draft(connection, data: Mapping) -> dict:
    with transaction(connection):
        header, items = _prepare(connection, data)
        invoice_id = repository.insert_draft(connection, header)
        repository.replace_items(connection, invoice_id, items)
        repository.record_event(connection, invoice_id, "DRAFT_CREATED")
        result = get_invoice(connection, invoice_id)
    return result


def update_draft(connection, invoice_id: int, data: Mapping) -> dict:
    _check_fields(data, HEADER_INPUTS | {"items"})
    with transaction(connection):
        current = get_invoice(connection, invoice_id)
        if current["document_status"] != "DRAFT" or current["number"] is not None:
            raise InvoiceStateError("Hanya draft tanpa nomor yang dapat diedit.")
        merged = {key: current[key] for key in HEADER_INPUTS}
        merged["items"] = [
            {key: item[key] for key in ITEM_INPUTS}
            for item in current["items"]
        ]
        merged.update(data)
        header, items = _prepare(connection, merged)
        repository.update_draft(connection, invoice_id, header)
        repository.replace_items(connection, invoice_id, items)
        repository.record_event(connection, invoice_id, "DRAFT_UPDATED")
        result = get_invoice(connection, invoice_id)
    return result


def list_invoices(
    connection, search: str = "", status: str | None = None,
    limit: int = 100, offset: int = 0,
) -> list[dict]:
    search = _text(search, "Pencarian")
    if status is not None:
        status = _text(status, "Status", required=True).upper()
        if status not in INVOICE_STATUSES:
            raise InvoiceValidationError("Filter status tidak dikenal.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvoiceValidationError("Limit harus berupa bilangan bulat 1 sampai 100.")
    _integer(offset, "Offset", minimum=0)
    return repository.list_invoices(
        connection, search=search, status=status, limit=limit, offset=offset,
    )


def publish_invoice(database_path: str | Path, invoice_id: int) -> dict:
    database_path = Path(database_path).resolve()

    if not database_path.is_file():
        raise InvoiceValidationError("File database tidak ditemukan.")

    with closing(connect(database_path)) as connection:
        with ExitStack() as assets:
            with transaction(connection):
                current = get_invoice(connection, invoice_id)

                if (
                    current["document_status"] != "DRAFT"
                    or current["number"] is not None
                ):
                    raise InvoiceStateError(
                        "Hanya Draft tanpa nomor yang dapat diterbitkan."
                    )

                if not current["items"]:
                    raise InvoiceValidationError(
                        "Invoice harus memiliki minimal satu item."
                    )

                profile = get_business_profile(connection)

                if profile is None:
                    raise InvoiceValidationError(
                        "Simpan profile usaha sebelum menerbitkan invoice."
                    )

                _text(profile["name"], "Nama Usaha", required = True)

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
                    snapshot_business_images(database_path, profile)
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
                    ensure_ascii = False,
                    sort_keys = True,
                )

                repository.update_draft(
                    connection,
                    invoice_id,
                    header,
                )

                repository.replace_items(
                    connection,
                    invoice_id,
                    items,
                )

                number = allocate_document_number(
                    connection,
                    "INVOICE",
                    header["issue_date"],
                )

                repository.mark_issued(
                    connection,
                    invoice_id,
                    number,
                    business_snapshot,
                    customer_snapshot,
                )

                repository.record_event(
                    connection,
                    invoice_id,
                    "INVOICE ISSUED"
                )

                result = get_invoice(connection, invoice_id)

    return result


