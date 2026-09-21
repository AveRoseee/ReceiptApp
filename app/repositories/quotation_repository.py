import sqlite3
from typing import Any, Mapping


HEADER_COLUMNS = (
    "customer_id",
    "issue_date",
    "valid_until",
    "subtotal",
    "discount_type",
    "discount_amount",
    "discount_value",
    "tax_rate_bps",
    "tax_amount",
    "grand_total",
    "notes",
    "terms",
)

ITEM_COLUMNS = (
    "catalog_item_id",
    "position",
    "name_snapshot",
    "description",
    "quantity_milli",
    "unit",
    "unit_price",
    "line_total"
)


def get_quotation(
    connection: sqlite3.Connection,
    quotation_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM quotations 
        WHERE id = ?
        """,
        (quotation_id,),
    ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["items"] = [
        dict(item)
        for item in connection.execute(
            """
            SELECT * FROM quotation_items
            WHERE quotation_id = ?
            ORDER by position
            """,
            (quotation_id,),
        ).fetchall()
    ]

    return result


def insert_draft(connection, data: Mapping[str, Any]) -> int:
    columns = ", ".join(HEADER_COLUMNS)
    placeholders = ", ".join("?" for _ in HEADER_COLUMNS)

    cursor = connection.execute(
        f"INSERT INTO quotations ({columns}) VALUES ({placeholders})",
        tuple(data[column] for column in HEADER_COLUMNS)
    )

    return int(cursor.lastrowid)


def update_draft(connection, quotation_id: int, data) -> None:
    assignments = ", ".join(
        f"{column} = ?" for column in HEADER_COLUMNS
    )

    connection.execute(
        f"UPDATE quotations SET {assignments} WHERE id = ?",
        tuple(data[column] for column in HEADER_COLUMNS)
        + (quotation_id,),
    )


def replace_items(connection, quotation_id: int, items: list) -> None:
    connection.execute(
        "DELETE FROM quotation_items WHERE quotation_id = ?",
        (quotation_id,),
    )

    columns = ", ".join(("quotation_id", *ITEM_COLUMNS))
    placeholders = ", ".join(
        "?" for _ in range(len(ITEM_COLUMNS) + 1)
    )

    connection.executemany(
        f"INSERT INTO quotation_items ({columns}) VALUES ({placeholders})",
            [
                (quotation_id,)
                + tuple(item[column] for column in ITEM_COLUMNS)
                for item in items
            ],
    )


def record_event(
    connection,
    quotation_id: int,
    event_type: str,
) -> None:
    connection.execute(
        """
        INSERT INTO document_events (quotation_id, event_type)
        VALUES (?, ?)
        """,
        (quotation_id, event_type)
    )


def mark_sent(
    connection,
    quotation_id: int,
    number: str,
    business_snapshot: str,
    customer_snapshot: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE quotations
        SET number = ?,
            business_snapshot = ?,
            customer_snapshot = ?,
            status = 'SENT'
        WHERE id = ? AND status = 'DRAFT' AND number IS NULL
        """,
        (
            number,
            business_snapshot,
            customer_snapshot,
            quotation_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Penawaran tidak lagi berupa draft atau nomor."
        )


def change_sent_status(
    connection,
    quotation_id: int,
    new_status: str
) -> None:
    cursor = connection.execute(
        """
        UPDATE quotations
        SET status = ?
        WHERE id = ? AND status = 'SENT'
        """,
        (new_status, quotation_id),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Penawaran tidak lagi berstatus SENT."
        )


def list_quotations(
    connection,
    search: str = "",
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    rows = connection.execute(
        """
        WITH summaries AS (
            SELECT q.id, q.customer_id, q.number, q.issue_date,
                    q.valid_until, q.status, q.grand_total,
                    CASE
                        WHEN q.status = 'DRAFT' THEN c.name
                        ELSE coalesce(
                            json_extract(
                                q.customer_snapshot,
                                '$.data.name'
                            ),
                            ''
                        )
                    END AS customer_name
            FROM quotations AS q
            JOIN customers AS c ON c.id = q.customer_id
        )
        SELECT * FROM summaries
        WHERE (? IS NULL OR status = ?)
            AND (
                instr(lower(coalesce(number, '')), lower(?)) > 0
                OR instr(lower(customer_name), lower(?)) > 0
            )
        ORDER BY issue_date DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (
            status,
            status,
            search,
            search,
            limit,
            offset,
        ),
    ).fetchall()

    return [dict(row) for row in rows]