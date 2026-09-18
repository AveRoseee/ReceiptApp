import pytest

from app.database import connect, initialize_database
from app.services.business_profile_service import (
    ProfileValidationError,
    get_business_profile,
    save_business_profile,
)


@pytest.fixture
def connection(tmp_path):
    database_path = initialize_database(
        tmp_path / "business_profile.db"
    )

    database_connection = connect(database_path)

    try:
        yield database_connection
    finally:
        database_connection.close()


def test_create_profile_cleans_input(connection):
    profile = save_business_profile(
        connection,
        {
            "name": "  Usaha Contoh  ",
            "phone": "  081234567890  ",
            "bank_account_number": "0012345678",
        },
    )

    assert profile["id"] == 1
    assert profile["name"] == "Usaha Contoh"
    assert profile["phone"] == "081234567890"
    assert profile["bank_account_number"] == "0012345678"


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_name_is_rejected(connection, name):
    with pytest.raises(
        ProfileValidationError,
        match="Nama usaha wajib diisi",
    ):
        save_business_profile(
            connection,
            {"name": name},
        )

    assert get_business_profile(connection) is None


def test_partial_update_preserves_other_fields(connection):
    save_business_profile(
        connection,
        {
            "name": "Usaha Lama",
            "address": "Bandung",
            "bank_name": "BCA",
        },
    )

    updated = save_business_profile(
        connection,
        {"name": "Usaha Baru"},
    )

    assert updated["name"] == "Usaha Baru"
    assert updated["address"] == "Bandung"
    assert updated["bank_name"] == "BCA"


def test_invalid_update_keeps_previous_profile(connection):
    save_business_profile(
        connection,
        {
            "name": "Usaha Contoh",
            "address": "Bandung",
        },
    )

    with pytest.raises(ProfileValidationError):
        save_business_profile(
            connection,
            {
                "name": "   ",
                "address": "Jakarta",
            },
        )

    profile = get_business_profile(connection)

    assert profile["name"] == "Usaha Contoh"
    assert profile["address"] == "Bandung"
    assert not connection.in_transaction


@pytest.mark.parametrize(
    "data",
    [
        {"id": "2"},
        {"phone": 81234567890},
        {"unknown_field": "contoh"},
    ],
)
def test_invalid_fields_are_rejected(connection, data):
    with pytest.raises(ProfileValidationError):
        save_business_profile(connection, data)

    assert get_business_profile(connection) is None


def test_optional_field_can_be_cleared(connection):
    save_business_profile(
        connection,
        {
            "name": "Usaha Contoh",
            "phone": "081234567890",
        },
    )

    updated = save_business_profile(
        connection,
        {"phone": ""},
    )

    assert updated["phone"] == ""
    assert updated["name"] == "Usaha Contoh"