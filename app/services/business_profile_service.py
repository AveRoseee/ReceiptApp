import sqlite3
from typing import Any, Mapping

from app.database import transaction
from app.repositories import business_profile_repository as repository

class ProfileValidationError(ValueError):
    """Input Profil usaha tidak memenuhi Aturan"""

TEXT_FIELDS = {
    "name",
    "address",
    "phone",
    "whatsapp",
    "email",
    "website",
    "npwp",
    "bank_name",
    "bank_account_name",
    "bank_account_number",
    "responsible_person",
}

ASSET_FIELDS = {
    "logo_path",
    "qris_path",
    "signature_path",
    "stamp_path",
}

ALLOWED_FIELDS = TEXT_FIELDS | ASSET_FIELDS


def _clean_input(
    data: Mapping[str, str | None],
) -> dict[str, str | None]:
    cleaned: dict[str, str | None] = {}

    for field, value in data.items():
        if field not in ALLOWED_FIELDS:
            raise ProfileValidationError(
                f"Field Profil tidak dikenal: {field}"
            )
        
        if value is not None and not isinstance(value, str):
            raise ProfileValidationError(
                f"Nilai '{field}' harus berupa teks."
            )
        
        normalized = value.strip() if value is not None else ""

        if field in ASSET_FIELDS:
            cleaned[field] = normalized or None
        else:
            cleaned[field] = normalized

    return cleaned


def get_business_profile(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    
    return repository.get_profile(connection)


def save_business_profile(
    connection: sqlite3.Connection,
    data: Mapping[str, str | None],
) -> dict[str, Any]:
    
    cleaned = _clean_input(data)

    with transaction(connection):
        current_profile = repository.get_profile(connection)

        if current_profile is None:
            merged: dict[str, str | None] = {}
        else:
            merged = {
                field: current_profile[field]
                for field in ALLOWED_FIELDS
            }

        merged.update(cleaned)

        name = merged.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ProfileValidationError(
                "Nama usaha wajib diisi."
            )

        merged["name"] = name.strip()

        repository.save_profile(connection, merged)

        saved_profile = repository.get_profile(connection)

        if saved_profile is None:
            raise RuntimeError(
                "Profil usaha tidak ditemukan setelah disimpan."
            )

        return saved_profile