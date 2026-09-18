import sqlite3
from typing import Any, Mapping


CUSTOMER_COLUMNS = (
    "type",
    "name",
    "company_name",
    "address",
    "phone",
    "whatsapp",
    "email",
    "npwp",
    "notes",
)


def get_customer(
    connection: sqlite3.Connection,
    customer_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()

    return dict(row) if row is not None else None


def list_customers(
    connection: sqlite3.Connection,
    search: str = "",
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM customers
        WHERE (? = 1 OR is_active = 1)
            AND (
                instr(lower(name), lower(?)) > 0
                OR instr(lower(company_name), lower(?)) > 0
                OR instr(phone, ?) > 0
                OR instr(whatsapp, ?) > 0
            )
        ORDER BY name COLLATE NOCASE, id
        LIMIT ? OFFSET ?
        """,
        (
            int(include_archived),
            search,
            search,
            search,
            search,
            limit,
            offset,
        ),
    ).fetchall()

    return [dict(row) for row in rows]


def create_customer(
    connection: sqlite3.Connection,
    data: Mapping[str, str],
) -> int:
    columns = ", ".join(CUSTOMER_COLUMNS)
    placeholders = ", ".join("?" for _ in CUSTOMER_COLUMNS)

    cursor = connection.execute(
        f"""
        INSERT INTO customers ({columns})
        VALUES ({placeholders})
        """,
        tuple(data[column] for column in CUSTOMER_COLUMNS),
    )

    return int(cursor.lastrowid)


def update_customer(
    connection: sqlite3.Connection,
    customer_id: int,
    data: Mapping[str, str],
) -> None:
    assignments = ", ".join(
        f"{column} = ?"
        for column in CUSTOMER_COLUMNS
    )

    parameters = tuple(
        data[column]
        for column in CUSTOMER_COLUMNS
    ) + (customer_id, )

    connection.execute(
        f"""
        UPDATE customers
        SET {assignments}
        WHERE id = ?
        """,
        parameters,
    )


def set_customer_active(
    connection: sqlite3.Connection,
    customer_id: int,
    active: bool
) -> None:
    connection.execute(
        """
        UPDATE customers
        SET is_active = ?
        WHERE id = ?
        """,
        (int(active), customer_id),
    )