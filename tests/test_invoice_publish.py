from contextlib import closing
import json
import sqlite3

from PIL import Image
import pytest

from app.database import connect, initialize_database
from app.repositories import invoice_repository
from app.services import catalog_service, customer_service
from app.services.business_image_service import (
    BusinessImageValidationError,
    IMAGE_TYPES,
    save_business_image,
)
from app.services.business_profile_service import save_business_profile
from app.services.invoice_service import (
    InvoiceNotFoundError,
    InvoiceStateError,
    InvoiceValidationError,
    create_draft,
    get_invoice,
    publish_invoice,
    update_draft,
)


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "business.db")

    with closing(connect(path)) as connection:
        save_business_profile(connection, {"name": "Usaha Lama"})

        customer = customer_service.create_customer(
            connection,
            {"name": "Pelanggan Lama"},
        )
        catalog = catalog_service.create_item(
            connection,
            {
                "name": "Barang Titipan",
                "default_price": 100000,
                "unit": "pcs",
            },
        )
        draft = create_draft(
            connection,
            {
                "customer_id": customer["id"],
                "issue_date": "2026-09-24",
                "items": [
                    {
                        "catalog_item_id": catalog["id"],
                        "quantity_milli": 2000,
                    }
                ],
            },
        )

        yield {
            "path": path,
            "connection": connection,
            "draft": draft,
            "customer": customer,
            "catalog": catalog,
        }


def publish(case):
    return publish_invoice(case["path"], case["draft"]["id"])


def add_images(case):
    source = case["path"].parent / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)

    for image_type in IMAGE_TYPES:
        save_business_image(case["path"], source, image_type)

    return source.read_bytes()


def assert_rolled_back(case):
    connection = case["connection"]

    assert get_invoice(
        connection,
        case["draft"]["id"],
    ) == case["draft"]

    assert connection.execute(
        "SELECT last_value FROM document_sequences "
        "WHERE document_type = 'INVOICE'"
    ).fetchone()[0] == 0

    assert connection.execute(
        "SELECT COUNT(*) FROM document_events "
        "WHERE event_type = 'INVOICE_ISSUED'"
    ).fetchone()[0] == 0

    directory = case["path"].parent / "assets" / "documents"
    assert list(directory.rglob("*")) == []


def test_publish_without_images(case):
    result = publish(case)

    assert result["document_status"] == "ISSUED"
    assert result["number"] == "INV-2026-0001"
    assert result["quotation_id"] is None
    assert result["grand_total"] == 200000

    business = json.loads(result["business_snapshot"])
    customer = json.loads(result["customer_snapshot"])

    assert business["schema_version"] == customer["schema_version"] == 1
    assert business["data"]["name"] == "Usaha Lama"
    assert customer["data"]["name"] == "Pelanggan Lama"

    for field, _ in IMAGE_TYPES.values():
        assert business["data"][field] is None

    assert case["connection"].execute(
        "SELECT COUNT(*) FROM document_events "
        "WHERE invoice_id = ? AND event_type = 'INVOICE_ISSUED'",
        (result["id"],),
    ).fetchone()[0] == 1


def test_snapshots_survive_master_changes(case):
    original_image = add_images(case)
    result = publish(case)
    connection = case["connection"]

    save_business_profile(connection, {"name": "Usaha Baru"})
    customer_service.update_customer(
        connection,
        case["customer"]["id"],
        {"name": "Pelanggan Baru"},
    )
    catalog_service.update_item(
        connection,
        case["catalog"]["id"],
        {
            "name": "Barang Baru",
            "default_price": 999999,
        },
    )

    replacement = case["path"].parent / "replacement.png"
    Image.new("RGB", (8, 8), "red").save(replacement)
    snapshot = json.loads(result["business_snapshot"])["data"]

    for image_type, (field, _) in IMAGE_TYPES.items():
        save_business_image(case["path"], replacement, image_type)

        assert snapshot[field].startswith("assets/documents/")
        assert (
            case["path"].parent / snapshot[field]
        ).read_bytes() == original_image

    assert get_invoice(connection, result["id"]) == result


def test_publish_recalculates_and_preserves_draft_price(case):
    catalog_service.update_item(
        case["connection"],
        case["catalog"]["id"],
        {"default_price": 999999},
    )
    case["connection"].execute(
        "UPDATE invoices SET subtotal = 0, grand_total = 0 WHERE id = ?",
        (case["draft"]["id"],),
    )

    result = publish(case)

    assert result["items"][0]["unit_price"] == 100000
    assert result["items"][0]["line_total"] == 200000
    assert result["subtotal"] == result["grand_total"] == 200000


def test_issued_invoice_cannot_be_republished_or_edited(case):
    first = publish(case)

    with pytest.raises(InvoiceStateError):
        publish(case)

    with pytest.raises(InvoiceStateError):
        update_draft(
            case["connection"],
            first["id"],
            {"notes": "Perubahan"},
        )

    assert get_invoice(case["connection"], first["id"]) == first

    assert case["connection"].execute(
        "SELECT last_value FROM document_sequences "
        "WHERE document_type = 'INVOICE'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "reason",
    ["empty", "profile", "customer", "catalog"],
)
def test_invalid_draft_is_rejected(case, reason):
    connection = case["connection"]

    if reason == "empty":
        case["draft"] = update_draft(
            connection,
            case["draft"]["id"],
            {"items": []},
        )
    elif reason == "profile":
        connection.execute("DELETE FROM business_profile")
    elif reason == "customer":
        customer_service.set_customer_active(
            connection,
            case["customer"]["id"],
            False,
        )
    else:
        catalog_service.set_item_active(
            connection,
            case["catalog"]["id"],
            False,
        )

    with pytest.raises(InvoiceValidationError):
        publish(case)

    assert_rolled_back(case)


def test_cancelled_draft_cannot_be_published(case):
    case["connection"].execute(
        "UPDATE invoices SET document_status = 'CANCELLED' WHERE id = ?",
        (case["draft"]["id"],),
    )
    case["draft"] = get_invoice(
        case["connection"],
        case["draft"]["id"],
    )

    with pytest.raises(InvoiceStateError):
        publish(case)

    assert_rolled_back(case)


def test_missing_image_rolls_back_partial_copies(case):
    add_images(case)

    relative = case["connection"].execute(
        "SELECT qris_path FROM business_profile WHERE id = 1"
    ).fetchone()[0]

    (case["path"].parent / relative).unlink()

    with pytest.raises(BusinessImageValidationError):
        publish(case)

    assert_rolled_back(case)


@pytest.mark.parametrize("failure", ["event", "commit"])
def test_failure_rolls_back_and_allows_retry(case, monkeypatch, failure):
    add_images(case)
    original = invoice_repository.record_event

    def fail(connection, invoice_id, event_type):
        if failure == "event":
            raise RuntimeError("Simulasi gagal")

        original(connection, invoice_id, event_type)

        # Menunda pemeriksaan foreign key sampai commit.
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            "INSERT INTO document_events (invoice_id, event_type) "
            "VALUES (999999, 'TEST_INVALID_REFERENCE')"
        )

    with monkeypatch.context() as patch:
        patch.setattr(invoice_repository, "record_event", fail)

        error = (
            RuntimeError
            if failure == "event"
            else sqlite3.IntegrityError
        )

        with pytest.raises(error):
            publish(case)

    assert_rolled_back(case)
    assert publish(case)["number"] == "INV-2026-0001"


@pytest.mark.parametrize("invoice_id", [0, True, "1"])
def test_invalid_id_is_rejected(case, invoice_id):
    with pytest.raises(InvoiceValidationError):
        publish_invoice(case["path"], invoice_id)

    assert_rolled_back(case)


def test_unknown_invoice_is_rejected(case):
    with pytest.raises(InvoiceNotFoundError):
        publish_invoice(case["path"], 999999)

    assert_rolled_back(case)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(InvoiceValidationError):
        publish_invoice(path, 1)

    assert not path.exists()


def test_numbers_increase_independently_of_quotations(case):
    assert publish(case)["number"] == "INV-2026-0001"

    second = create_draft(
        case["connection"],
        {
            "customer_id": case["customer"]["id"],
            "issue_date": "2026-09-24",
            "items": [
                {
                    "name_snapshot": "Jasa Titip",
                    "unit": "layanan",
                    "quantity_milli": 1000,
                    "unit_price": 100000,
                }
            ],
        },
    )

    result = publish_invoice(case["path"], second["id"])

    assert result["number"] == "INV-2026-0002"
    assert case["connection"].execute(
        "SELECT last_value FROM document_sequences "
        "WHERE document_type = 'QUOTATION'"
    ).fetchone()[0] == 0