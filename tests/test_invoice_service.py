from copy import deepcopy

import pytest

from app.database import connect, initialize_database, transaction
from app.repositories import invoice_repository
from app.services import catalog_service, customer_service
from app.services.invoice_service import (
    InvoiceNotFoundError,
    InvoiceStateError,
    InvoiceValidationError,
    create_draft,
    get_invoice,
    list_invoices,
    update_draft,
)


@pytest.fixture
def connection(tmp_path):
    database_path = initialize_database(
        tmp_path / "invoices.db"
    )
    connection = connect(database_path)

    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def payload(connection):
    customer = customer_service.create_customer(
        connection,
        {"name": "Pelanggan Contoh"},
    )
    catalog = catalog_service.create_item(
        connection,
        {
            "name": "Jasa Desain",
            "type": "SERVICE",
            "unit": "jam",
            "default_price": 100000,
        },
    )

    return {
        "customer_id": customer["id"],
        "issue_date": "2026-09-20",
        "due_date": "2026-09-30",
        "items": [
            {
                "catalog_item_id": catalog["id"],
                "quantity_milli": 1500,
            }
        ],
    }


def test_create_draft_copies_catalog_and_calculates(
    connection,
    payload,
):
    original = deepcopy(payload)
    result = create_draft(connection, payload)

    assert result["document_status"] == "DRAFT"
    assert result["number"] is None
    assert result["grand_total"] == 150000
    assert result["items"][0]["name_snapshot"] == "Jasa Desain"
    assert result["items"][0]["unit"] == "jam"
    assert result["items"][0]["unit_price"] == 100000
    assert result["items"][0]["line_total"] == 150000
    assert get_invoice(connection, result["id"]) == result
    assert payload == original

    counter = connection.execute(
        """
        SELECT last_value FROM document_sequences
        WHERE document_type = 'INVOICE'
        """
    ).fetchone()[0]

    assert counter == 0


def test_empty_draft_is_allowed(connection, payload):
    payload["items"] = []

    result = create_draft(connection, payload)

    assert result["items"] == []
    assert result["grand_total"] == 0


def test_manual_item_and_percentage_totals(connection, payload):
    payload["items"] = [
        {
            "name_snapshot": "Konsultasi",
            "unit": "jam",
            "quantity_milli": 2000,
            "unit_price": 100000,
        }
    ]
    payload.update(
        discount_type="PERCENT",
        discount_value=1000,
        tax_rate_bps=1100,
    )

    result = create_draft(connection, payload)

    assert result["subtotal"] == 200000
    assert result["discount_amount"] == 20000
    assert result["tax_amount"] == 19800
    assert result["grand_total"] == 199800
    assert result["items"][0]["catalog_item_id"] is None


def test_partial_update_preserves_catalog_snapshot(
    connection,
    payload,
):
    draft = create_draft(connection, payload)
    catalog_id = payload["items"][0]["catalog_item_id"]

    catalog_service.update_item(
        connection,
        catalog_id,
        {
            "name": "Nama Baru",
            "default_price": 999999,
        },
    )

    updated = update_draft(
        connection,
        draft["id"],
        {"notes": "Catatan baru"},
    )

    assert updated["notes"] == "Catatan baru"
    assert updated["items"][0]["name_snapshot"] == "Jasa Desain"
    assert updated["items"][0]["unit_price"] == 100000
    assert updated["grand_total"] == 150000


def test_replace_items_recalculates_total(connection, payload):
    draft = create_draft(connection, payload)

    updated = update_draft(
        connection,
        draft["id"],
        {
            "items": [
                {
                    "name_snapshot": "Item A",
                    "unit": "pcs",
                    "quantity_milli": 1000,
                    "unit_price": 20000,
                },
                {
                    "name_snapshot": "Item B",
                    "unit": "pcs",
                    "quantity_milli": 2000,
                    "unit_price": 30000,
                },
            ]
        },
    )

    assert [
        item["position"] for item in updated["items"]
    ] == [1, 2]

    assert [
        item["line_total"] for item in updated["items"]
    ] == [20000, 60000]

    assert updated["grand_total"] == 80000


@pytest.mark.parametrize(
    "changes",
    [
        {"issue_date": "2026-02-30"},
        {"due_date": "2026-09-19"},
        {"customer_id": True},
        {"customer_id": 999},
        {"number": "INV-2026-0001"},
        {"document_status": "SENT"},
        {"grand_total": 1},
        {"discount_value": 150001},
        {"items": "invalid"},
        {
            "items": [
                {
                    "catalog_item_id": 999,
                    "quantity_milli": 1000,
                }
            ]
        },
        {
            "items": [
                {
                    "name_snapshot": "Item",
                    "unit": "pcs",
                    "quantity_milli": 0,
                    "unit_price": 100,
                }
            ]
        },
        {
            "items": [
                {
                    "name_snapshot": "Item",
                    "unit": "pcs",
                    "quantity_milli": 1000,
                    "unit_price": 2**63,
                }
            ]
        },
    ],
)
def test_invalid_input_creates_no_draft(
    connection,
    payload,
    changes,
):
    payload.update(changes)

    with pytest.raises(InvoiceValidationError):
        create_draft(connection, payload)

    assert connection.execute(
        "SELECT COUNT(*) FROM invoices"
    ).fetchone()[0] == 0

    assert connection.execute(
        "SELECT COUNT(*) FROM invoice_items"
    ).fetchone()[0] == 0


def test_archived_customer_is_rejected(connection, payload):
    customer_service.set_customer_active(
        connection,
        payload["customer_id"],
        False,
    )

    with pytest.raises(InvoiceValidationError):
        create_draft(connection, payload)


def test_archived_catalog_is_rejected(connection, payload):
    catalog_service.set_item_active(
        connection,
        payload["items"][0]["catalog_item_id"],
        False,
    )

    with pytest.raises(InvoiceValidationError):
        create_draft(connection, payload)


def test_failed_create_rolls_back_header_and_items(
    connection,
    payload,
    monkeypatch,
):
    def fail_event(*args):
        raise RuntimeError("Simulasi gagal")

    monkeypatch.setattr(
        invoice_repository,
        "record_event",
        fail_event,
    )

    with pytest.raises(RuntimeError, match="Simulasi"):
        create_draft(connection, payload)

    for table in (
        "invoices",
        "invoice_items",
        "document_events",
    ):
        assert connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0] == 0

    assert not connection.in_transaction


def test_failed_update_restores_previous_draft(
    connection,
    payload,
    monkeypatch,
):
    draft = create_draft(connection, payload)

    def fail_event(*args):
        raise RuntimeError("Simulasi gagal")

    monkeypatch.setattr(
        invoice_repository,
        "record_event",
        fail_event,
    )

    with pytest.raises(RuntimeError, match="Simulasi"):
        update_draft(
            connection,
            draft["id"],
            {
                "notes": "Tidak tersimpan",
                "items": [],
            },
        )

    assert get_invoice(connection, draft["id"]) == draft

    assert connection.execute(
        "SELECT COUNT(*) FROM document_events"
    ).fetchone()[0] == 1


def test_issued_invoice_cannot_be_edited(connection, payload):
    draft = create_draft(connection, payload)

    # Menyiapkan status untuk tes; bukan implementasi penerbitan.
    with transaction(connection):
        connection.execute(
            """
            UPDATE invoices
            SET number = ?, document_status = 'ISSUED'
            WHERE id = ?
            """,
            ("INV-TEST-0001", draft["id"]),
        )

    with pytest.raises(InvoiceStateError):
        update_draft(
            connection,
            draft["id"],
            {"notes": "Perubahan"},
        )


def test_missing_invoice(connection):
    with pytest.raises(InvoiceNotFoundError):
        get_invoice(connection, 999)

@pytest.mark.parametrize("changes", [
    {"quotation_id": 1},
    {"business_snapshot": "{}"},
    {"due_date": ""},
    {"issue_date": "20260923"},
    {"notes": None},
    {"tax_rate_bps": True},
    {"items": [None]},
    {"items": [{"name_snapshot": "Barang", "unit": "pcs", "quantity_milli": True, "unit_price": 1}]},
    {"items": [{"name_snapshot": "Barang", "unit": "pcs", "quantity_milli": 1000, "unit_price": -1}]},
])
def test_additional_invalid_fields_are_rejected(connection, payload, changes):
    payload.update(changes)
    with pytest.raises(InvoiceValidationError):
        create_draft(connection, payload)
    assert connection.execute("SELECT count(*) FROM invoices").fetchone()[0] == 0


def test_jastip_items_remain_individual_and_do_not_need_quotation(connection, payload):
    payload["items"] = [
        {"name_snapshot": name, "unit": "pcs", "quantity_milli": 1000, "unit_price": price}
        for name, price in [
            ("Action Figure", 150000), ("Sepatu Gunung", 450000),
            ("Jasa Titip", 100000), ("Pengiriman", 25000),
        ]
    ]
    result = create_draft(connection, payload)
    assert result["quotation_id"] is None
    assert len(result["items"]) == 4
    assert result["grand_total"] == 725000
    assert result["document_status"] == "DRAFT"
    assert result["number"] is None


def test_cancelled_draft_cannot_be_edited(connection, payload):
    draft = create_draft(connection, payload)
    connection.execute(
        "UPDATE invoices SET document_status = 'CANCELLED' WHERE id = ?",
        (draft["id"],),
    )
    with pytest.raises(InvoiceStateError):
        update_draft(connection, draft["id"], {"notes": "Tidak tersimpan"})


def test_partial_update_preserves_date_and_can_clear_due_date(connection, payload):
    draft = create_draft(connection, payload)
    updated = update_draft(connection, draft["id"], {"notes": "Catatan"})
    assert updated["due_date"] == payload["due_date"]
    updated = update_draft(connection, draft["id"], {"due_date": None})
    assert updated["due_date"] is None
    assert updated["issue_date"] == payload["issue_date"]


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True},
    {"offset": -1}, {"offset": True}, {"offset": "0"},
    {"status": "SENT"}, {"status": ""}, {"status": False},
    {"search": None},
])
def test_invalid_list_options(connection, options):
    with pytest.raises(InvoiceValidationError):
        list_invoices(connection, **options)


def test_list_pagination_filters_and_historical_names(connection, payload):
    import json
    drafts = [create_draft(connection, payload) for _ in range(3)]
    snapshot = json.dumps({"schema_version": 1, "data": {"name": "Nama Historis"}})
    connection.execute(
        """UPDATE invoices SET number = 'INV-2026-0001',
           customer_snapshot = ?, document_status = 'ISSUED' WHERE id = ?""",
        (snapshot, drafts[0]["id"]),
    )
    connection.execute(
        "UPDATE invoices SET document_status = 'CANCELLED' WHERE id = ?",
        (drafts[1]["id"],),
    )
    customer_service.update_customer(
        connection, payload["customer_id"], {"name": "Nama Sekarang"},
    )
    assert [row["id"] for row in list_invoices(connection, limit=1)] == [drafts[2]["id"]]
    assert [row["id"] for row in list_invoices(connection, limit=1, offset=1)] == [drafts[1]["id"]]
    assert len(list_invoices(connection, status=" draft ")) == 1
    assert list_invoices(connection, status="ISSUED")[0]["customer_name"] == "Nama Historis"
    assert list_invoices(connection, status="CANCELLED")[0]["customer_name"] == "Nama Sekarang"
    assert len(list_invoices(connection, search="nama sekarang")) == 2
    assert len(list_invoices(connection, search="INV-2026")) == 1
    assert list_invoices(connection, search="%") == []


@pytest.mark.parametrize("invoice_id", [0, -1, True, "1", 2**63])
def test_invalid_invoice_ids(connection, invoice_id):
    with pytest.raises(InvoiceValidationError):
        get_invoice(connection, invoice_id)
