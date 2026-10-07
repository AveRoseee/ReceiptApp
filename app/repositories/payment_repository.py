import json
from typing import Mapping


PAYMENT_COLUMNS = ("payment_date", "amount", "method", "reference", "notes")


def get_payment(connection, payment_id):
    row = connection.execute(
        "SELECT * FROM payments WHERE id = ?",
        (payment_id,),
    ).fetchone()

    return dict(row) if row is not None else None


def get_by_request_id(connection, request_id):
    row = connection.execute(
        """
        SELECT p.*
        FROM document_events AS e
        JOIN payments AS p
            ON p.id = json_extract(e.details, '$.payment_id')
        WHERE e.event_type = 'PAYMENT_RECORDED'
            AND json_extract(e.details, '$.request_id') = ?
        ORDER BY e.id
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()

    return dict(row) if row is not None else None


def get_invoice_summary(connection, invoice_id):
    row = connection.execute(
        """
        SELECT i.id AS invoice_id, i.number, i.issue_date,
                i.document_status, b.grand_total, b.paid_amount,
                b.balance_due, b.settlement_status,
                b.payment_status, b.is_overdue
        FROM invoices AS i
        JOIN invoice_balances as b ON b.invoice_id = i.id
        WHERE i.id = ?
        """,
        (invoice_id, ),
    ).fetchone()

    return dict(row) if row is not None else None


def insert_payment(connection, invoice_id, data: Mapping):
    cursor = connection.execute(
        """
        INSERT INTO payments (
            invoice_id, payment_date, amount, method, reference, notes
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (invoice_id,) + tuple(data[key] for key in PAYMENT_COLUMNS)
    )

    return int(cursor.lastrowid)


def list_payments(connection, invoice_id, include_void = True, limit = 100, offset = 0):
    rows  = connection.execute(
        """
        SELECT p.*, r.id AS receipt_id, r.number AS receipt_number, r.status AS receipt_status
        FROM payments p LEFT JOIN receipts r ON r.payment_id = p.id
        WHERE p.invoice_id = ? AND (? = 1 OR p.status = 'VALID')
        ORDER BY p.payment_date DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        (invoice_id, int(include_void), limit, offset),
    ).fetchall()

    return [dict(row) for row in rows]


def get_valid_receipt(connection, payment_id):
    row = connection.execute(
        """
        SELECT * FROM receipts WHERE payment_id = ? AND status = 'VALID'
        """,
        (payment_id, ),
    ).fetchone()

    return dict(row) if row is not None else None


def mark_receipt_void(connection, receipt_id, reason):
    cursor = connection.execute(
        """
        UPDATE receipts SET status = 'VOID', void_reason = ?
        WHERE id = ? AND status = 'VALID'
        """,
        (reason, receipt_id, ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Kwitansi tidak lagi aktif")


def mark_payment_void(connection, payment_id, reason):
    cursor = connection.execute(
        """
        UPDATE payments SET status = 'VOID', void_reason = ?
        WHERE id = ? AND status = 'VALID'
        """,
        (reason, payment_id)
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Pembayaran tidak lagi aktif.")


def record_event(connection, invoice_id, event_type, details):
    connection.execute(
        """
        INSERT INTO document_events (invoice_id, event_type, details)
        VALUES (?, ?, ?)
        """,
        (invoice_id, event_type, json.dumps(details, ensure_ascii = False, sort_keys = True))
    )


def record_receipt_event(connection, receipt_id, details):
    connection.execute(
        """
        INSERT INTO document_events (receipt_id, event_type, details)
        VALUES (?, 'RECEIPT_VOIDED', ?)
        """,
        (receipt_id, json.dumps(details, ensure_ascii = False, sort_keys = True))
    )
