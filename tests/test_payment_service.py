from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from threading import Barrier
from uuid import uuid4

import pytest

from app.database import connect, initialize_database
from app.repositories import payment_repository as repository
from app.services import customer_service, invoice_service
from app.services.business_profile_service import save_business_profile
from app.services.payment_service import (
    PaymentNotFoundError,
    PaymentStateError,
    PaymentValidationError,
    get_invoice_summary,
    get_payment,
    list_payments,
    record_payment,
    void_payment,
)


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "payments.db")
    with closing(connect(path)) as connection:
        save_business_profile(connection, {"name": "Jastip Jepang"})
        customer = customer_service.create_customer(connection, {"name": "Pembeli"})
        draft = invoice_service.create_draft(
            connection,
            {
                "customer_id": customer["id"],
                "issue_date": "2026-09-24",
                "items": [{
                    "name_snapshot": "Titipan, jasa, dan pengiriman",
                    "quantity_milli": 1000, "unit": "paket", "unit_price": 725000,
                }],
            },
        )
        invoice_service.publish_invoice(path, draft["id"])
        yield {
            "path": path, "connection": connection,
            "invoice_id": draft["id"], "customer_id": customer["id"],
        }


def data(amount=300000, **changes):
    result = {
        "payment_date": "2026-09-24", "amount": amount,
        "method": "TRANSFER", "reference": "TRX-01", "notes": "DP",
    }
    result.update(changes)
    return result


def pay(case, amount=300000, request_id=None, **changes):
    return record_payment(
        case["connection"], case["invoice_id"], data(amount, **changes),
        request_id=request_id or str(uuid4()),
    )


def summary(case):
    return get_invoice_summary(case["connection"], case["invoice_id"])


def events(case):
    return [
        dict(row) for row in case["connection"].execute(
            "SELECT * FROM document_events ORDER BY id"
        )
    ]


def add_receipt(case, payment_id):
    cursor = case["connection"].execute(
        """
        INSERT INTO receipts (payment_id, number, issued_date, document_snapshot)
        VALUES (?, 'RCPT-2026-0001', '2026-09-24', '{}')
        """,
        (payment_id,),
    )
    return cursor.lastrowid


def test_dp_cicilan_pelunasan(case):
    assert summary(case)["settlement_status"] == "UNPAID"
    pay(case, 300000)
    assert summary(case)["paid_amount"] == 300000
    assert summary(case)["balance_due"] == 425000
    assert summary(case)["settlement_status"] == "PARTIAL"
    pay(case, 200000)
    assert summary(case)["balance_due"] == 225000
    pay(case, 225000)
    assert summary(case)["paid_amount"] == 725000
    assert summary(case)["balance_due"] == 0
    assert summary(case)["settlement_status"] == "PAID"
    assert summary(case)["document_status"] == "ISSUED"
    assert len(list_payments(case["connection"], case["invoice_id"])) == 3


@pytest.mark.parametrize("method", ["CASH", "TRANSFER", "QRIS", "OTHER", " transfer "])
def test_metode_dan_normalisasi(case, method):
    result = pay(case, method=method, reference="  ABC  ", notes="  DP  ")
    assert result["method"] == method.strip().upper()
    assert result["reference"] == "ABC"
    assert result["notes"] == "DP"


@pytest.mark.parametrize("changes", [
    {"amount": 0}, {"amount": -1}, {"amount": True},
    {"amount": "300000"}, {"amount": 1.5}, {"amount": 2**63},
    {"payment_date": "24/09/2026"}, {"payment_date": "2026-02-30"},
    {"payment_date": "20260924"}, {"payment_date": None},
    {"method": "CARD"}, {"method": ""}, {"method": None},
    {"reference": None}, {"notes": 123}, {"status": "VALID"},
])
def test_input_salah_tidak_mengubah_saldo_atau_riwayat(case, changes):
    before = events(case)
    with pytest.raises(PaymentValidationError):
        pay(case, **changes)
    assert summary(case)["paid_amount"] == 0
    assert list_payments(case["connection"], case["invoice_id"]) == []
    assert events(case) == before


@pytest.mark.parametrize("payload", [None, [], {}, {"amount": 100}])
def test_struktur_atau_field_wajib_tidak_lengkap(case, payload):
    with pytest.raises(PaymentValidationError):
        record_payment(
            case["connection"], case["invoice_id"], payload,
            request_id=str(uuid4()),
        )
    assert summary(case)["paid_amount"] == 0


def test_overpayment_dan_invoice_lunas_ditolak(case):
    with pytest.raises(PaymentValidationError):
        pay(case, 725001)
    pay(case, 725000)
    before = events(case)
    with pytest.raises(PaymentStateError):
        pay(case, 1)
    assert summary(case)["balance_due"] == 0
    assert events(case) == before


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED"])
def test_invoice_belum_terbit_atau_dibatalkan_ditolak(case, status):
    connection = case["connection"]
    draft = invoice_service.create_draft(connection, {
        "customer_id": case["customer_id"], "issue_date": "2026-09-24",
    })
    if status == "CANCELLED":
        connection.execute(
            "UPDATE invoices SET document_status = 'CANCELLED' WHERE id = ?",
            (draft["id"],),
        )
    with pytest.raises(PaymentStateError):
        record_payment(connection, draft["id"], data(), request_id=str(uuid4()))


def test_permintaaan_ulang_tidak_menggandakan_pembayaran(case):
    request_id = str(uuid4())
    original = data(725000)
    unchanged = deepcopy(original)
    first = record_payment(
        case["connection"], case["invoice_id"], original, request_id=request_id,
    )
    second = record_payment(
        case["connection"], case["invoice_id"], original, request_id=request_id,
    )
    assert first == second
    assert original == unchanged
    assert len(list_payments(case["connection"], case["invoice_id"])) == 1
    recorded = [e for e in events(case) if e["event_type"] == "PAYMENT_RECORDED"]
    assert len(recorded) == 1
    assert json.loads(recorded[0]["details"])["payment_id"] == first["id"]


def test_request_id_tidak_boleh_dipakai_untuk_data_lain(case):
    request_id = str(uuid4())
    pay(case, request_id=request_id)
    with pytest.raises(PaymentValidationError):
        pay(case, 100000, request_id=request_id)
    assert summary(case)["paid_amount"] == 300000


@pytest.mark.parametrize("request_id", ["", "abc", None, 123])
def test_request_id_tidak_valid(case, request_id):
    with pytest.raises(PaymentValidationError):
        record_payment(
            case["connection"], case["invoice_id"], data(), request_id=request_id,
        )


def test_dua_pembayaran_sama_nominal_tetap_boleh_jika_berbeda_permintaaan(case):
    first = pay(case)
    second = pay(case)
    assert first["id"] != second["id"]
    assert summary(case)["paid_amount"] == 600000


def test_batal_mengembalikan_saldo_dan_menyimpan_alasan(case):
    request_id = str(uuid4())
    payment = pay(case, 725000, request_id=request_id)
    cancelled = void_payment(case["connection"], payment["id"], "  Salah nominal  ")
    assert cancelled["status"] == "VOID"
    assert cancelled["void_reason"] == "Salah nominal"
    assert cancelled["amount"] == 725000
    assert summary(case)["settlement_status"] == "UNPAID"
    assert summary(case)["balance_due"] == 725000
    assert len(list_payments(case["connection"], case["invoice_id"])) == 1
    assert list_payments(case["connection"], case["invoice_id"], include_void=False) == []
    with pytest.raises(PaymentStateError):
        void_payment(case["connection"], payment["id"], "Ulang")
    with pytest.raises(PaymentStateError):
        pay(case, 725000, request_id=request_id)
    pay(case, 300000)
    assert summary(case)["balance_due"] == 425000


@pytest.mark.parametrize("reason", ["", "   ", None, 123])
def test_alasan_pembatalan_wajib_diisi(case, reason):
    payment = pay(case)
    with pytest.raises(PaymentValidationError):
        void_payment(case["connection"], payment["id"], reason)
    assert get_payment(case["connection"], payment["id"])["status"] == "VALID"


def test_pembatalan_juga_membatalkan_kwitansi(case):
    payment = pay(case)
    receipt_id = add_receipt(case, payment["id"])
    void_payment(case["connection"], payment["id"], "Salah transfer")
    receipt = case["connection"].execute(
        "SELECT * FROM receipts WHERE id = ?", (receipt_id,),
    ).fetchone()
    assert receipt["status"] == "VOID"
    assert receipt["void_reason"] == "Salah transfer"
    assert any(e["receipt_id"] == receipt_id and e["event_type"] == "RECEIPT_VOIDED"
               for e in events(case))
    assert summary(case)["paid_amount"] == 0


@pytest.mark.parametrize("operation", ["record", "void"])
@pytest.mark.parametrize("failure", ["event", "commit"])
def test_gagal_operasi_mengembalikan_semua_data(case, monkeypatch, operation, failure):
    connection = case["connection"]
    payment = pay(case) if operation == "void" else None
    if payment:
        add_receipt(case, payment["id"])
    before_payments = list_payments(connection, case["invoice_id"])
    before_events = events(case)
    before_balance = summary(case)
    before_receipts = [dict(r) for r in connection.execute("SELECT * FROM receipts")]
    original = repository.record_event

    def fail(connection, *args):
        if failure == "event":
            raise RuntimeError("Simulasi gagal")
        original(connection, *args)
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            "INSERT INTO document_events (invoice_id, event_type) "
            "VALUES (999999, 'INVALID_REFERENCE')"
        )

    with monkeypatch.context() as patch:
        patch.setattr(repository, "record_event", fail)
        expected = RuntimeError if failure == "event" else sqlite3.IntegrityError
        with pytest.raises(expected):
            if payment:
                void_payment(connection, payment["id"], "Keliru")
            else:
                pay(case)
    assert list_payments(connection, case["invoice_id"]) == before_payments
    assert events(case) == before_events
    assert summary(case) == before_balance
    assert [dict(r) for r in connection.execute("SELECT * FROM receipts")] == before_receipts


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True},
    {"offset": -1}, {"offset": True}, {"include_void": "yes"},
])
def test_filter_daftar_tidak_valid(case, options):
    with pytest.raises(PaymentValidationError):
        list_payments(case["connection"], case["invoice_id"], **options)


def test_paginasi_dan_urutan_riwayat(case):
    first = pay(case, 100000, payment_date="2026-09-24")
    second = pay(case, 100000, payment_date="2026-09-25")
    third = pay(case, 100000, payment_date="2026-09-25")
    page = list_payments(case["connection"], case["invoice_id"], limit=2)
    assert [p["id"] for p in page] == [third["id"], second["id"]]
    page = list_payments(case["connection"], case["invoice_id"], limit=2, offset=2)
    assert [p["id"] for p in page] == [first["id"]]


def test_id_tidak_ditemukan(case):
    connection = case["connection"]
    with pytest.raises(PaymentNotFoundError):
        get_payment(connection, 999999)
    with pytest.raises(PaymentNotFoundError):
        void_payment(connection, 999999, "Keliru")
    with pytest.raises(invoice_service.InvoiceNotFoundError):
        get_invoice_summary(connection, 999999)
    with pytest.raises(invoice_service.InvoiceNotFoundError):
        record_payment(connection, 999999, data(), request_id=str(uuid4()))


@pytest.mark.parametrize("value", [0, True, "1", 2**63])
def test_id_tidak_valid(case, value):
    with pytest.raises(PaymentValidationError):
        get_payment(case["connection"], value)
    with pytest.raises(PaymentValidationError):
        get_invoice_summary(case["connection"], value)


@pytest.mark.parametrize("same_request", [True, False])
def test_dua_koneksi_menyimpan_bersamaan(case, same_request):
    barrier = Barrier(2)
    common_id = str(uuid4())

    def worker():
        with closing(connect(case["path"])) as connection:
            barrier.wait(timeout=5)
            try:
                return record_payment(
                    connection, case["invoice_id"], data(600000),
                    request_id=common_id if same_request else str(uuid4()),
                )
            except (PaymentValidationError, PaymentStateError) as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert summary(case)["paid_amount"] == 600000
    assert len(list_payments(case["connection"], case["invoice_id"])) == 1
    if same_request:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(result, dict) for result in results) == 1


def test_data_tetap_ada_setelah_koneksi_dibuka_ulang(case):
    payment = pay(case)
    with closing(connect(case["path"])) as connection:
        assert get_payment(connection, payment["id"]) == payment
        assert get_invoice_summary(connection, case["invoice_id"])["balance_due"] == 425000
