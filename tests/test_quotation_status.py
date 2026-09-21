from contextlib import closing
from datetime import date, timedelta

import pytest

from app.database import connect, initialize_database
from app.repositories import quotation_repository
from app.services import customer_service
from app.services.business_profile_service import (
    save_business_profile,
)
from app.services.quotation_service import (
    QuotationStateError,
    QuotationValidationError,
    change_quotation_status,
    create_draft,
    get_quotation,
    list_quotations,
    publish_quotation,
)


TODAY = date(2026, 9, 21)


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "status.db")

    with closing(connect(path)) as connection:
        save_business_profile(
            connection,
            {"name": "Usaha Contoh"},
        )
        customer = customer_service.create_customer(
            connection,
            {"name": "Pelanggan Lama"},
        )

        data = {
            "customer_id": customer["id"],
            "issue_date": "2026-09-20",
            "valid_until": TODAY.isoformat(),
            "items": [
                {
                    "name_snapshot": "Jasa Desain",
                    "unit": "project",
                    "quantity_milli": 1000,
                    "unit_price": 100000,
                }
            ],
        }

        draft = create_draft(connection, data)
        sent = publish_quotation(path, draft["id"])

        yield {
            "connection": connection,
            "path": path,
            "data": data,
            "sent": sent,
            "customer": customer,
        }


@pytest.mark.parametrize("target", ["ACCEPTED", "REJECTED"])
def test_status_change_preserves_document_and_records_event(
    case,
    target,
):
    connection = case["connection"]
    before = case["sent"]

    result = change_quotation_status(
        connection,
        before["id"],
        target,
        today=TODAY,
    )

    assert result == {**before, "status": target}

    event = connection.execute(
        """
        SELECT event_type FROM document_events
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()[0]

    assert event == f"QUOTATION_{target}"

    assert connection.execute(
        """
        SELECT last_value FROM document_sequences
        WHERE document_type = 'QUOTATION'
        """
    ).fetchone()[0] == 1


def test_expire_only_after_last_valid_day(case):
    connection = case["connection"]
    quotation_id = case["sent"]["id"]

    with pytest.raises(QuotationStateError):
        change_quotation_status(
            connection,
            quotation_id,
            "EXPIRED",
            today=TODAY,
        )

    result = change_quotation_status(
        connection,
        quotation_id,
        "EXPIRED",
        today=TODAY + timedelta(days=1),
    )

    assert result["status"] == "EXPIRED"


def test_cannot_accept_after_expiry_date(case):
    with pytest.raises(QuotationStateError):
        change_quotation_status(
            case["connection"],
            case["sent"]["id"],
            "ACCEPTED",
            today=TODAY + timedelta(days=1),
        )

    assert get_quotation(
        case["connection"],
        case["sent"]["id"],
    ) == case["sent"]


def test_no_expiry_date_cannot_be_marked_expired(case):
    data = {
        **case["data"],
        "valid_until": None,
    }

    draft = create_draft(case["connection"], data)
    sent = publish_quotation(case["path"], draft["id"])

    with pytest.raises(QuotationStateError):
        change_quotation_status(
            case["connection"],
            sent["id"],
            "EXPIRED",
            today=TODAY,
        )


def test_draft_cannot_skip_publication(case):
    draft = create_draft(
        case["connection"],
        case["data"],
    )

    with pytest.raises(QuotationStateError):
        change_quotation_status(
            case["connection"],
            draft["id"],
            "ACCEPTED",
            today=TODAY,
        )


def test_accepted_status_cannot_be_changed_manually(case):
    connection = case["connection"]
    quotation_id = case["sent"]["id"]

    change_quotation_status(
        connection,
        quotation_id,
        "ACCEPTED",
        today=TODAY,
    )

    with pytest.raises(QuotationStateError):
        change_quotation_status(
            connection,
            quotation_id,
            "REJECTED",
            today=TODAY,
        )


@pytest.mark.parametrize(
    "target",
    ["DRAFT", "SENT", "CONVERTED", "OTHER", None],
)
def test_invalid_manual_target_is_rejected(case, target):
    with pytest.raises(QuotationValidationError):
        change_quotation_status(
            case["connection"],
            case["sent"]["id"],
            target,
            today=TODAY,
        )


def test_event_failure_rolls_back_status(case, monkeypatch):
    def fail_event(*args):
        raise RuntimeError("Simulasi gagal")

    monkeypatch.setattr(
        quotation_repository,
        "record_event",
        fail_event,
    )

    with pytest.raises(RuntimeError, match="Simulasi"):
        change_quotation_status(
            case["connection"],
            case["sent"]["id"],
            "ACCEPTED",
            today=TODAY,
        )

    assert get_quotation(
        case["connection"],
        case["sent"]["id"],
    ) == case["sent"]

    assert case["connection"].execute(
        """
        SELECT COUNT(*) FROM document_events
        WHERE event_type = 'QUOTATION_ACCEPTED'
        """
    ).fetchone()[0] == 0


def test_status_is_normalized(case):
    result = change_quotation_status(
        case["connection"],
        case["sent"]["id"],
        " accepted ",
        today=TODAY,
    )

    assert result["status"] == "ACCEPTED"


def test_search_uses_snapshot_for_published_document(case):
    connection = case["connection"]

    customer_service.update_customer(
        connection,
        case["customer"]["id"],
        {"name": "Pelanggan Baru"},
    )
    draft = create_draft(connection, case["data"])

    old_name = list_quotations(
        connection,
        search="pelanggan lama",
    )
    new_name = list_quotations(
        connection,
        search="pelanggan baru",
    )
    by_number = list_quotations(
        connection,
        search="quO-2026-0001",
    )

    assert [
        row["id"] for row in old_name
    ] == [case["sent"]["id"]]

    assert [
        row["id"] for row in new_name
    ] == [draft["id"]]

    assert [
        row["id"] for row in by_number
    ] == [case["sent"]["id"]]

    assert list_quotations(connection, search="%") == []


def test_filter_and_pagination(case):
    connection = case["connection"]

    first = create_draft(connection, case["data"])
    second = create_draft(connection, case["data"])

    page_one = list_quotations(
        connection,
        status=" draft ",
        limit=1,
    )
    page_two = list_quotations(
        connection,
        status="DRAFT",
        limit=1,
        offset=1,
    )

    assert [
        row["id"] for row in page_one
    ] == [second["id"]]

    assert [
        row["id"] for row in page_two
    ] == [first["id"]]

    assert list_quotations(
        connection,
        status="ACCEPTED",
    ) == []


@pytest.mark.parametrize(
    "options",
    [
        {"search": None},
        {"status": "OTHER"},
        {"status": ""},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"offset": -1},
        {"offset": True},
        {"offset": 2**63},
    ],
)
def test_invalid_list_options_are_rejected(case, options):
    with pytest.raises(QuotationValidationError):
        list_quotations(
            case["connection"],
            **options,
        )