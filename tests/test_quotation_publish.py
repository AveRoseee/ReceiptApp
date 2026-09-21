from contextlib import closing
import json
import sqlite3

from PIL import Image
import pytest

from app.database import connect, initialize_database
from app.repositories import quotation_repository
from app.services import catalog_service, customer_service
from app.services.business_image_service import (
    BusinessImageValidationError,
    IMAGE_TYPES,
    save_business_image,
)
from app.services.business_profile_service import (
    save_business_profile,
)
from app.services.quotation_service import (
    QuotationStateError,
    QuotationValidationError,
    create_draft,
    get_quotation,
    publish_quotation,
    update_draft,
)


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "business.db")

    with closing(connect(path)) as connection:
        save_business_profile(
            connection,
            {"name": "Usaha Lama"},
        )
        customer = customer_service.create_customer(
            connection,
            {"name": "Pelanggan Lama"},
        )
        catalog = catalog_service.create_item(
            connection,
            {
                "name": "Jasa Lama",
                "default_price": 100000,
                "unit": "jam",
            },
        )
        draft = create_draft(
            connection,
            {
                "customer_id": customer["id"],
                "issue_date": "2026-09-21",
                "items": [
                    {
                        "catalog_item_id": catalog["id"],
                        "quantity_milli": 1500,
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


def add_images(case):
    source = case["path"].parent / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)

    for image_type in IMAGE_TYPES:
        save_business_image(
            case["path"],
            source,
            image_type,
        )

    return source.read_bytes()


def document_files(case):
    directory = case["path"].parent / "assets" / "documents"

    return (
        list(directory.rglob("*"))
        if directory.exists()
        else []
    )


def assert_rolled_back(case):
    connection = case["connection"]
    current = get_quotation(
        connection,
        case["draft"]["id"],
    )

    assert current == case["draft"]

    assert connection.execute(
        """
        SELECT last_value FROM document_sequences
        WHERE document_type = 'QUOTATION'
        """
    ).fetchone()[0] == 0

    assert connection.execute(
        """
        SELECT COUNT(*) FROM document_events
        WHERE event_type = 'QUOTATION_SENT'
        """
    ).fetchone()[0] == 0

    assert document_files(case) == []


def test_publish_without_images(case):
    result = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )

    assert result["status"] == "SENT"
    assert result["number"] == "QUO-2026-0001"
    assert result["grand_total"] == 150000

    business = json.loads(result["business_snapshot"])
    customer = json.loads(result["customer_snapshot"])

    assert business["schema_version"] == 1
    assert business["data"]["name"] == "Usaha Lama"
    assert customer["data"]["name"] == "Pelanggan Lama"
    assert business["data"]["logo_path"] is None
    assert document_files(case) == []


def test_snapshots_survive_master_and_image_changes(case):
    original_image = add_images(case)
    result = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )
    snapshot = json.loads(result["business_snapshot"])["data"]

    connection = case["connection"]

    save_business_profile(
        connection,
        {"name": "Usaha Baru"},
    )
    customer_service.update_customer(
        connection,
        case["customer"]["id"],
        {"name": "Pelanggan Baru"},
    )
    catalog_service.update_item(
        connection,
        case["catalog"]["id"],
        {
            "name": "Jasa Baru",
            "default_price": 999999,
        },
    )

    replacement = case["path"].parent / "replacement.png"
    Image.new("RGB", (8, 8), "red").save(replacement)

    for image_type, (field, _) in IMAGE_TYPES.items():
        save_business_image(
            case["path"],
            replacement,
            image_type,
        )

        relative_path = snapshot[field]
        assert relative_path.startswith("assets/documents/")

        saved_image = case["path"].parent / relative_path
        assert saved_image.read_bytes() == original_image

    assert get_quotation(connection, result["id"]) == result


def test_publish_recalculates_stored_totals(case):
    case["connection"].execute(
        """
        UPDATE quotations
        SET subtotal = 0, grand_total = 0
        WHERE id = ?
        """,
        (case["draft"]["id"],),
    )

    result = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )

    assert result["subtotal"] == 150000
    assert result["grand_total"] == 150000


def test_publish_keeps_draft_price_when_catalog_changes(case):
    catalog_service.update_item(
        case["connection"],
        case["catalog"]["id"],
        {"default_price": 999999},
    )

    result = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )

    assert result["items"][0]["unit_price"] == 100000
    assert result["grand_total"] == 150000


def test_cannot_publish_twice_or_edit_sent_quotation(case):
    first = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )

    with pytest.raises(QuotationStateError):
        publish_quotation(case["path"], first["id"])

    with pytest.raises(QuotationStateError):
        update_draft(
            case["connection"],
            first["id"],
            {"notes": "Perubahan"},
        )

    assert case["connection"].execute(
        """
        SELECT last_value FROM document_sequences
        WHERE document_type = 'QUOTATION'
        """
    ).fetchone()[0] == 1


def test_empty_draft_cannot_be_published(case):
    case["draft"] = update_draft(
        case["connection"],
        case["draft"]["id"],
        {"items": []},
    )

    with pytest.raises(QuotationValidationError):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)


def test_business_profile_is_required(case):
    case["connection"].execute("DELETE FROM business_profile")

    with pytest.raises(QuotationValidationError):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)


@pytest.mark.parametrize("kind", ["customer", "catalog"])
def test_archived_reference_is_rejected(case, kind):
    if kind == "customer":
        customer_service.set_customer_active(
            case["connection"],
            case["customer"]["id"],
            False,
        )
    else:
        catalog_service.set_item_active(
            case["connection"],
            case["catalog"]["id"],
            False,
        )

    with pytest.raises(QuotationValidationError):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)


def test_missing_image_cleans_already_copied_images(case):
    add_images(case)

    qris_path = case["connection"].execute(
        "SELECT qris_path FROM business_profile WHERE id = 1"
    ).fetchone()[0]

    (case["path"].parent / qris_path).unlink()

    with pytest.raises(BusinessImageValidationError):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)


def test_event_failure_rolls_back_database_and_files(
    case,
    monkeypatch,
):
    add_images(case)
    original = quotation_repository.record_event

    def fail_event(*args):
        raise RuntimeError("Simulasi gagal")

    monkeypatch.setattr(
        quotation_repository,
        "record_event",
        fail_event,
    )

    with pytest.raises(RuntimeError, match="Simulasi"):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)

    monkeypatch.setattr(
        quotation_repository,
        "record_event",
        original,
    )

    result = publish_quotation(
        case["path"],
        case["draft"]["id"],
    )

    assert result["number"] == "QUO-2026-0001"


def test_commit_failure_also_cleans_files(case, monkeypatch):
    add_images(case)
    original = quotation_repository.record_event

    def defer_invalid_reference(
        connection,
        quotation_id,
        event_type,
    ):
        original(connection, quotation_id, event_type)

        # Sengaja menunda pemeriksaan foreign key sampai commit.
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO document_events (quotation_id, event_type)
            VALUES (999999, 'TEST_INVALID_REFERENCE')
            """
        )

    monkeypatch.setattr(
        quotation_repository,
        "record_event",
        defer_invalid_reference,
    )

    with pytest.raises(sqlite3.IntegrityError):
        publish_quotation(case["path"], case["draft"]["id"])

    assert_rolled_back(case)