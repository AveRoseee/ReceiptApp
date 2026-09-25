from datetime import date
from typing import Mapping
from uuid import UUID

from app.database import transaction
from app.repositories import payment_repository as repository
from app.services.invoice_service import InvoiceNotFoundError


MAX_INTEGER = 2**63 - 1
PAYMENT_METHODS = {"CASH", "TRANSFER", "QRIS", "OTHER"}
INPUT_FIELDS = {
    "payment_date",
    "amount",
    "method",
    "reference",
    "notes",
}


class PaymentValidationError(ValueError):
    """Isian pembayaran tidak valid."""


class PaymentNotFoundError(LookupError):
    """Pembayaran tidak ditemukan."""


class PaymentStateError(ValueError):
    "Status invoice atau pembayaran tidak mengizinkan tindakan."


def _integer(value, label, minimum = 1):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise PaymentValidationError(
            f"{label} harus berupa bilangan bulat antara {minimum} dan {MAX_INTEGER}"
        )
    return value


def _text(value, label, required = False):
    if not isinstance(value, str):
        raise PaymentValidationError(f"{label} harus berupa teks.")

    value = value.strip()
    if required and not value:
        raise PaymentValidationError(f"{label} wajib diisi.")
    return value


def _date(value):
    value = _text(value, "Tanggal pembayaran", required = True)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise PaymentValidationError(
            "Tanggal pembayaran harus valid dalam format YYYY-MM-DD."
        ) from error
    if parsed.isoformat() != value:
        raise PaymentValidationError("Gunakan format tanggal YYYY-MM-DD.")
    return value


def _request_id(value):
    value = _text(value, "ID Permintaan", required = True)

    try:
        return str(UUID(value))
    except ValueError as error:
        raise PaymentValidationError(
            "ID Permintaan harus berupa UUID."
        ) from error


def _prepare(data):
    if not isinstance(data, Mapping) or set(data) - INPUT_FIELDS:
        raise PaymentValidationError("Struktur atau field pembayaran tidak sesuai.")
    method = _text(data.get("method"), "Metode Pembayaran", required = True).upper()
    if method not in PAYMENT_METHODS:
        raise PaymentValidationError("Metode pembayaran tidak dikenal.")
    return {
        "payment_date": _date(data.get("payment_date")),
        "amount": _integer(data.get("amount"), "Nominal Pembayaran"),
        "method": method,
        "reference": _text(data.get("reference", ""), "Referensi"),
        "notes": _text(data.get("notes", ""), "Catatan"),
    }


def get_payment(connection, payment_id: int) -> dict:
    _integer(payment_id, "ID Pembayaran")
    payment = repository.get_payment(connection, payment_id)
    if payment is None:
        raise PaymentNotFoundError("Pembayaran tidak ditemukan.")
    return payment


def get_invoice_summary(connection, invoice_id: int) -> dict:
    _integer(invoice_id, "ID invoice")
    result = repository.get_invoice_summary(connection, invoice_id)
    if result is None:
        raise InvoiceNotFoundError("Invoice tidak ditemukan.")
    return result


def list_payments(
    connection, invoice_id: int, *,
    include_void: bool = True, limit: int = 100, offset: int = 0
) -> list[dict]:
    get_invoice_summary(connection, invoice_id)
    if type(include_void) is not bool:
        raise PaymentValidationError("Pilihan serta pembatalan harus boolean.")
    if type(limit) is not int  or not 1 <= limit <= 100:
        raise PaymentValidationError("Limit harus berupa angka dari 1 sampai 100.")
    _integer(offset, "Offset", minimum = 0)
    return repository.list_payments(
        connection, invoice_id, include_void = include_void, limit = limit, offset = offset
    )


def record_payment(
        connection, invoice_id: int, data: Mapping, *, request_id: str,
) -> dict:
    _integer(invoice_id, "ID Invoice")
    prepared = _prepare(data)
    request_id = _request_id(request_id)

    with transaction(connection):
        previous = repository.get_by_request_id(connection, request_id)
        if previous is not None:
            if previous["invoice_id"] != invoice_id or any(
                previous[key] != prepared[key] for key in prepared
            ):
                raise PaymentValidationError(
                    "ID permintaan sudah digunakan untuk pembayaran berbeda."
                )
            if previous["status"] != "VALID":
                raise PaymentStateError(
                    "Pembayaran dari permintaan ini sudah dibatalkan."
                )
            return previous

        summary = get_invoice_summary(connection, invoice_id)
        if summary["document_status"] != "ISSUED":
            raise PaymentStateError(
                "Pembayaran hanya dapat dicatat pada invoice yang diterbitkan."
            )
        if summary["balance_due"] == 0:
            raise PaymentStateError("Invoice sudah lunas.")
        if prepared["amount"] > summary["balance_due"]:
            raise PaymentValidationError(
                "Nominal pembayaran melebihi sisa tagihan."
            )

        payment_id = repository.insert_payment(connection, invoice_id, prepared)
        repository.record_event(
            connection, invoice_id, "PAYMENT_RECORDED",
            {
                "payment_id": payment_id,
                "request_id": request_id,
                "amount": prepared["amount"],
            },
        )
        result = get_payment(connection, payment_id)

    return result


def void_payment(connection, payment_id: int, reason: str) -> dict:
    _integer(payment_id, "ID Pembayaran")
    reason = _text(reason, "Alasan Pembatalan", required = True)

    with transaction(connection):
        current = get_payment(connection, payment_id)
        if current["status"] != "VALID":
            raise PaymentStateError("Pembayaran sudah dibatalkan.")

        receipt = repository.get_valid_receipt(connection, payment_id)
        if receipt is not None:
            repository.mark_receipt_void(
                connection, 
                receipt["id"],
                reason,
            )
            repository.record_receipt_event(
                connection, receipt["id"],
                {"payment_id": payment_id, "reason": reason},
            )

        repository.mark_payment_void(connection, payment_id, reason)
        repository.record_event(
            connection, current["invoice_id"], "PAYMENT_VOIDED",
            {"payment_id": payment_id, "amount": current["amount"], "reason": reason}
        )
        result = get_payment(connection, payment_id)

    return result
    