import sqlite3
from typing import Any, Mapping


PROFILE_DEFAULTS: dict[str, str | None] = {
    "name": "",
    "address": "",
    "phone": "",
    "whatsapp": "",
    "email": "",
    "website": "",
    "npwp": "",
    "bank_name": "",
    "bank_account_name": "",
    "bank_account_number": "",
    "responsible_person": "",
    "logo_path": None,
    "qris_path": None,
    "signature_path": None,
    "stamp_path": None,
}

def get_profile(
    connection: sqlite3.Connection
) -> dict[str, str | Any]:
    cursor = connection.execute(
        """
        SELECT *
        FROM business_profile
        WHERE id = ?
        """,
        (1,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    column_names = [
        column[0]
        for column in cursor.description
    ]

    return dict(zip(column_names, row))


def save_profile(
    connection: sqlite3.Connection, data: Mapping[str, str | None],
) -> None:
    unknown_fields = set(data) - set(PROFILE_DEFAULTS)

    if unknown_fields:
        field_names = ", ".join(sorted(unknown_fields))
        raise ValueError(
            f"Field profil tidak dikenal: {field_names}"
        )

    values = {
        **PROFILE_DEFAULTS,
        **data,
    }

    columns = tuple(PROFILE_DEFAULTS)
    column_names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)

    update_assignments = ", ".join(
        f"{column} = excluded.{column}"
        for column in columns
    )

    sql = f"""
        INSERT INTO business_profile (
            id,
            {column_names}
        )
        VALUES (
            1,
            {placeholders}
        )
        ON CONFLICT(id) DO UPDATE SET
            {update_assignments}
    """

    parameters = tuple(
        values[column]
        for column in columns
    )

    connection.execute(sql, parameters)