from contextlib import closing
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database import connect
from app.services import receipt_service as receipts, payment_service as payments
from app.services.business_profile_service import save_business_profile
from test_core_invoice import business, record


@pytest.mark.parametrize("value,words", [
    (0, "Nol rupiah"), (11, "Sebelas rupiah"), (12, "Dua belas rupiah"),
    (100, "Seratus rupiah"), (101, "Seratus satu rupiah"),
    (1000, "Seribu rupiah"), (1200, "Seribu dua ratus rupiah"),
    (725000, "Tujuh ratus dua puluh lima ribu rupiah"),
    (10**18, "Satu kuintiliun rupiah"),
])
def test_terbilang(value, words):
    assert receipts.terbilang(value) == words


@pytest.mark.parametrize("value", [True, -1, 1.5, "100", 2**63])
def test_invalid_terbilang(value):
    with pytest.raises(receipts.ReceiptValidationError):
        receipts.terbilang(value)


def test_issue_idempotent_snapshot_and_void(business):
    path, invoice = business
    with closing(connect(path)) as db:
        payment = record(db, invoice)
        first = receipts.issue_receipt(db, payment["id"], issued_date="2026-09-29")
        assert first == receipts.issue_receipt(db, payment["id"])
        assert first["number"] == "RCPT-2026-0001"
        save_business_profile(db, {"name": "Usaha Baru"})
        snapshot = receipts.read_snapshot(first["document_snapshot"])
        assert snapshot["business"]["name"] == "Usaha Awal"
        assert snapshot["payment"]["amount"] == 300000
        assert snapshot["invoice"]["number"] == invoice["number"]
        for sql in ("DELETE FROM receipts WHERE id=?", "UPDATE receipts SET description='changed' WHERE id=?"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, (first["id"],))
        payments.void_payment(db, payment["id"], "Salah input")
        voided = receipts.get_receipt(db, first["id"])
        assert voided["status"] == "VOID"
        assert voided["document_snapshot"] == first["document_snapshot"]
        with pytest.raises(receipts.ReceiptStateError):
            receipts.issue_receipt(db, payment["id"])


def test_number_and_data_rollback(business, monkeypatch):
    path, invoice = business
    def fail(*args):
        raise RuntimeError("audit unavailable")
    with closing(connect(path)) as db:
        payment = record(db, invoice)
        with monkeypatch.context() as patch:
            patch.setattr(receipts.repository, "record_event", fail)
            with pytest.raises(RuntimeError):
                receipts.issue_receipt(db, payment["id"])
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
        assert db.execute("SELECT last_value FROM document_sequences WHERE document_type='RECEIPT'").fetchone()[0] == 0
        receipt = receipts.issue_receipt(db, payment["id"])
        second = receipts.issue_receipt(db, record(db, invoice, 100000)["id"])
        assert receipt["number"] != second["number"]
        counters = dict(db.execute("SELECT document_type,last_value FROM document_sequences"))
        assert counters == {"QUOTATION": 0, "INVOICE": 1, "RECEIPT": 2}


def test_concurrent_issue_allocates_one_number(business):
    path, invoice = business
    with closing(connect(path)) as db:
        payment = record(db, invoice)
    def issue(_):
        with closing(connect(path)) as db:
            return receipts.issue_receipt(db, payment["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(issue, range(2)))
    assert results[0] == results[1]
