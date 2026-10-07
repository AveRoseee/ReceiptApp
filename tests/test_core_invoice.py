from contextlib import closing
from uuid import uuid4
import sqlite3

import pytest

from app.database import connect, initialize_database
from app.services import invoice_service as invoices, payment_service as payments
from app.services.business_profile_service import save_business_profile
from app.services.customer_service import create_customer


@pytest.fixture
def business(tmp_path):
    path = initialize_database(tmp_path / "business.db")
    with closing(connect(path)) as db:
        save_business_profile(db, {"name": "Usaha Awal"})
        customer = create_customer(db, {"name": "Pembeli Awal"})
        draft = invoices.create_draft(db, {
            "customer_id": customer["id"], "issue_date": "2026-09-29",
            "items": [{"name_snapshot": "Titipan", "unit": "pcs",
                       "quantity_milli": 1000, "unit_price": 725000}],
        })
    issued = invoices.publish_invoice(path, draft["id"])
    return path, issued


def record(db, invoice, amount=300000):
    return payments.record_payment(db, invoice["id"], {
        "payment_date": "2026-09-29", "amount": amount, "method": "TRANSFER",
    }, request_id=str(uuid4()))


def test_duplicate_has_no_number_and_preserves_line_values(business):
    path, invoice = business
    with closing(connect(path)) as db:
        duplicate = invoices.duplicate_as_draft(db, invoice["id"])
        assert duplicate["document_status"] == "DRAFT"
        assert duplicate["number"] is None
        assert duplicate["grand_total"] == 725000
        assert duplicate["items"][0]["unit_price"] == 725000
        assert duplicate["quotation_id"] is None
        assert db.execute("SELECT last_value FROM document_sequences WHERE document_type='INVOICE'").fetchone()[0] == 1


def test_duplicate_failure_rolls_back(business, monkeypatch):
    path, invoice = business
    def fail(*args, **kwargs):
        raise RuntimeError("after insert")
    monkeypatch.setattr(invoices.repository, "record_event", fail)
    with closing(connect(path)) as db:
        with pytest.raises(RuntimeError):
            invoices.duplicate_as_draft(db, invoice["id"])
        assert db.execute("SELECT count(*) FROM invoices").fetchone()[0] == 1


def test_cancel_requires_reason_and_voided_payments(business):
    path, invoice = business
    with closing(connect(path)) as db:
        with pytest.raises(invoices.InvoiceValidationError):
            invoices.cancel_invoice(db, invoice["id"], " ")
        payment = record(db, invoice)
        with pytest.raises(invoices.InvoiceStateError):
            invoices.cancel_invoice(db, invoice["id"], "Salah input")
        payments.void_payment(db, payment["id"], "Salah input")
        result = invoices.cancel_invoice(db, invoice["id"], "Salah input")
        assert result["document_status"] == "CANCELLED"
        assert result["number"] == invoice["number"]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM invoices WHERE id=?", (invoice["id"],))


def test_cancel_rolls_back_on_audit_failure(business, monkeypatch):
    path, invoice = business
    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(invoices.repository, "record_event", fail)
    with closing(connect(path)) as db:
        with pytest.raises(RuntimeError):
            invoices.cancel_invoice(db, invoice["id"], "Salah input")
        assert invoices.get_invoice(db, invoice["id"])["document_status"] == "ISSUED"
