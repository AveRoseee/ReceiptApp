import sqlite3
from typing import Any, Mapping


CATALOG_COLUMNS = (
    "sku",
    "type",
    "name",
    "description",
    "default_price",
    "unit",
)


def get_item(
    connection: sqlite3.Connection,
    item_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM catalog_items WHERE id = ?",
        (item_id, ),
    ).fetchone()

    return dict(row) if row is not None else None


def get_item_by_sku(
    connection: sqlite3.Connection,
    sku: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM catalog_items WHERE sku = ?",
        (sku,),
    ).fetchone()

    return dict(row) if row is not None else None


def list_items(
    connection: sqlite3.Connection,
    search: str = "",
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0, 
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM catalog_items
        WHERE (? = 1 OR is_active = 1)
            AND (
                instr(lower(name), lower(?)) > 0
                OR instr(lower(coalesce(sku, '')), lower(?)) > 0
                OR instr(lower(description), lower(?)) > 0
            )
        ORDER BY name COLLATE NOCASE, id
        LIMIT ? OFFSET ?
        """,
        (
            int(include_archived),
            search,
            search,
            search,
            limit,
            offset,
        ),
    ).fetchall()

    return [dict(row) for row in rows]


def create_item(
    connection: sqlite3.Connection,
    data: Mapping[str, Any],
) -> int:
    columns = ", ".join(CATALOG_COLUMNS)
    placeholders = ", ".join("?" for _ in CATALOG_COLUMNS)

    cursor = connection.execute(
        f"""
        INSERT INTO catalog_items({columns})
        VALUES ({placeholders})
        """,
        tuple(data[column] for column in CATALOG_COLUMNS)
    )

    return int(cursor.lastrowid)


def update_item(
    connection: sqlite3.Connection,
    item_id: int,
    data: Mapping[str, Any]
) -> None:
    assignments = ", ".join(
        f"{column} = ?"
        for column in CATALOG_COLUMNS
    )

    parameters = tuple(
        data[column]
        for column in CATALOG_COLUMNS
    ) + (item_id,)

    connection.execute(
        f"""
        UPDATE catalog_items
        SET {assignments}
        WHERE id = ?
        """,
        parameters,
    )


def set_item_active(
    connection: sqlite3.Connection,
    item_id: int,
    active: bool,
) -> None:
    connection.execute(
        """
        UPDATE catalog_items
        SET is_active = ?
        WHERE id = ?
        """,
        (int(active), item_id)
    )