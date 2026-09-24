"""SQLite queries for direct invoice drafts."""
from typing import Any, Mapping

HEADER_COLUMNS = (
    "customer_id", "issue_date", "due_date", "subtotal",
    "discount_type", "discount_value", "discount_amount",
    "tax_rate_bps", "tax_amount", "grand_total", "notes", "terms",
)
ITEM_COLUMNS = (
    "catalog_item_id", "position", "name_snapshot", "description",
    "quantity_milli", "unit", "unit_price", "line_total",
)


def get_invoice(connection, invoice_id: int) -> dict | None:
    row = connection.execute(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["items"] = [
        dict(item) for item in connection.execute(
            "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY position",
            (invoice_id,),
        ).fetchall()
    ]
    return result


def insert_draft(connection, data: Mapping[str, Any]) -> int:
    columns = ", ".join(HEADER_COLUMNS)
    placeholders = ", ".join("?" for _ in HEADER_COLUMNS)
    cursor = connection.execute(
        f"INSERT INTO invoices ({columns}) VALUES ({placeholders})",
        tuple(data[column] for column in HEADER_COLUMNS),
    )
    return int(cursor.lastrowid)


def update_draft(connection, invoice_id: int, data: Mapping) -> None:
    assignments = ", ".join(f"{column} = ?" for column in HEADER_COLUMNS)
    cursor = connection.execute(
        f"UPDATE invoices SET {assignments} "
        "WHERE id = ? AND document_status = 'DRAFT' AND number IS NULL",
        tuple(data[column] for column in HEADER_COLUMNS) + (invoice_id,),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Invoice tidak lagi berupa draft tanpa nomor.")


def replace_items(connection, invoice_id: int, items: list) -> None:
    connection.execute(
        "DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,),
    )
    columns = ", ".join(("invoice_id", *ITEM_COLUMNS))
    placeholders = ", ".join("?" for _ in range(len(ITEM_COLUMNS) + 1))
    connection.executemany(
        f"INSERT INTO invoice_items ({columns}) VALUES ({placeholders})",
        [
            (invoice_id,) + tuple(item[column] for column in ITEM_COLUMNS)
            for item in items
        ],
    )


def record_event(connection, invoice_id: int, event_type: str) -> None:
    connection.execute(
        "INSERT INTO document_events (invoice_id, event_type) VALUES (?, ?)",
        (invoice_id, event_type),
    )


def list_invoices(
    connection, search="", status=None, limit=100, offset=0,
) -> list[dict]:
    rows = connection.execute(
        """
        WITH summaries AS (
            SELECT i.id, i.customer_id, i.number, i.issue_date, i.due_date,
                   i.document_status, i.grand_total,
                   CASE WHEN i.number IS NULL THEN c.name
                        ELSE coalesce(
                            json_extract(i.customer_snapshot, '$.data.name'), ''
                        )
                   END AS customer_name
            FROM invoices AS i
            JOIN customers AS c ON c.id = i.customer_id
        )
        SELECT * FROM summaries
        WHERE (? IS NULL OR document_status = ?)
          AND (
              instr(lower(coalesce(number, '')), lower(?)) > 0
              OR instr(lower(customer_name), lower(?)) > 0
          )
        ORDER BY issue_date DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (status, status, search, search, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_issued(
        connection,
        invoice_id: int,
        number: str,
        business_snapshot: str,
        customer_snapshot: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE invoices
        SET document_status = 'ISSUED',
            number = ?,
            business_snapshot = ?,
            customer_snapshot = ?
        WHERE id = ?
            AND document_status = 'DRAFT'
            AND number IS NULL
        """,
        (number, business_snapshot, customer_snapshot, invoice_id),
    )

    if cursor.rowcount != 1:
        raise RuntimeError("Invoice tidak lagi berupa DRAFT tanpa nomor.")