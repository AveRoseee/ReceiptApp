"""Immutable receipts, one per payment; numbering and audit commit together."""
from datetime import date
import json

from app.database import transaction
from app.repositories import receipt_repository as repository
from app.services.invoice_service import get_invoice
from app.services.payment_service import get_payment
from app.services.document_number_service import allocate_document_number


class ReceiptValidationError(ValueError):
    pass


class ReceiptStateError(ValueError):
    pass


class ReceiptNotFoundError(LookupError):
    pass


def _id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ReceiptValidationError("ID kwitansi/pembayaran tidak valid.")


def terbilang(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ReceiptValidationError("Nominal harus integer Rupiah nonnegatif.")
    words = ("nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas")
    def spell(n):
        if n < 12:
            return words[n]
        if n < 20:
            return spell(n - 10) + " belas"
        if n < 100:
            q, r = divmod(n, 10)
            return spell(q) + " puluh" + (" " + spell(r) if r else "")
        if n < 200:
            return "seratus" + (" " + spell(n - 100) if n > 100 else "")
        if n < 1000:
            q, r = divmod(n, 100)
            return spell(q) + " ratus" + (" " + spell(r) if r else "")
        if n < 2000:
            return "seribu" + (" " + spell(n - 1000) if n > 1000 else "")
        for scale, label in ((10**18, "kuintiliun"), (10**15, "kuadriliun"),
                             (10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (1000, "ribu")):
            if n >= scale:
                q, r = divmod(n, scale)
                return spell(q) + " " + label + (" " + spell(r) if r else "")
    return spell(value).capitalize() + " rupiah"


def read_snapshot(raw):
    try:
        value = json.loads(raw)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError
        data = value["data"]
        for key in ("business", "customer", "payment", "invoice"):
            if not isinstance(data[key], dict):
                raise ValueError
        if not data["business"]["name"] or not data["customer"]["name"]:
            raise ValueError
        terbilang(data["payment"]["amount"])
        return data
    except (KeyError, TypeError, ValueError) as error:
        raise ReceiptValidationError("Salinan data kwitansi tidak valid.") from error


def get_receipt(connection, receipt_id):
    _id(receipt_id)
    result = repository.get_receipt(connection, receipt_id)
    if result is None:
        raise ReceiptNotFoundError("Kwitansi tidak ditemukan.")
    return result


def get_by_payment(connection, payment_id):
    _id(payment_id)
    return repository.get_by_payment(connection, payment_id)


def issue_receipt(connection, payment_id, *, issued_date=None, description=""):
    _id(payment_id)
    issued_date = date.today().isoformat() if issued_date is None else issued_date
    try:
        if date.fromisoformat(issued_date).isoformat() != issued_date:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ReceiptValidationError("Tanggal kwitansi tidak valid.") from error
    if not isinstance(description, str):
        raise ReceiptValidationError("Keterangan harus berupa teks.")
    with transaction(connection):
        payment = get_payment(connection, payment_id)
        if payment["status"] != "VALID":
            raise ReceiptStateError("Pembayaran dibatalkan tidak dapat dibuatkan kwitansi.")
        existing = repository.get_by_payment(connection, payment_id)
        if existing:
            if existing["status"] != "VALID":
                raise ReceiptStateError("Kwitansi pembayaran ini telah dibatalkan.")
            return existing
        invoice = get_invoice(connection, payment["invoice_id"])
        if invoice["document_status"] != "ISSUED":
            raise ReceiptStateError("Kwitansi memerlukan invoice terbit.")
        try:
            business = json.loads(invoice["business_snapshot"])
            customer = json.loads(invoice["customer_snapshot"])
            if business["schema_version"] != 1 or customer["schema_version"] != 1:
                raise ValueError
            snapshot = {"schema_version": 1, "data": {
                "business": business["data"], "customer": customer["data"],
                "payment": payment, "invoice": invoice,
                "amount_words": terbilang(payment["amount"]),
            }}
            read_snapshot(json.dumps(snapshot))
        except (TypeError, KeyError, ValueError) as error:
            raise ReceiptValidationError("Salinan invoice tidak dapat dibaca.") from error
        number = allocate_document_number(connection, "RECEIPT", issued_date)
        receipt_id = repository.insert_receipt(connection, payment_id, number,
                                               issued_date, description.strip(), snapshot)
        repository.record_event(connection, receipt_id)
        return get_receipt(connection, receipt_id)
