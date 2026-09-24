from contextlib import closing
from types import SimpleNamespace
import sqlite3

import flet as ft
import pytest

from app.database import connect
from app.services import invoice_service
from app.services.business_profile_service import save_business_profile
from app.views.invoice_view import build_invoice_view
from test_view_interactions import (
    FakePage,
    click,
    create_customer,
    database_path,
    find,
    texts,
    walk,
)


class DialogPage(FakePage):
    def show_dialog(self, dialog):
        dialog.open = True
        super().show_dialog(dialog)

    def pop_dialog(self):
        dialog = next(
            (
                item
                for item in reversed(self.dialogs)
                if item.open
            ),
            None,
        )

        if dialog is not None:
            dialog.open = False

        return dialog


@pytest.fixture
def case(database_path):
    customer = create_customer(database_path)

    with closing(connect(database_path)) as connection:
        save_business_profile(
            connection,
            {"name": "Jastip Jepang"},
        )
        draft = invoice_service.create_draft(
            connection,
            {
                "customer_id": customer["id"],
                "issue_date": "2026-09-24",
                "items": [
                    {
                        "name_snapshot": "Action Figure",
                        "unit": "pcs",
                        "quantity_milli": 1000,
                        "unit_price": 150000,
                    }
                ],
            },
        )

    page = DialogPage()
    view = build_invoice_view(page, database_path)
    click(view, "Lihat Detail")

    return database_path, draft, page, view


def action(dialog, label):
    button = next(
        item
        for item in dialog.actions
        if item.content == label
    )
    assert not button.disabled
    button.on_click(SimpleNamespace(control=button))


def saved(case):
    path, draft, _, _ = case

    with closing(connect(path)) as connection:
        return invoice_service.get_invoice(
            connection,
            draft["id"],
        )


def has_button(view, label):
    return any(
        isinstance(item, (ft.Button, ft.TextButton))
        and item.content == label
        for item in walk(view)
    )


def test_publish_requires_confirmation_and_cancel_keeps_draft(case):
    _, draft, page, view = case

    click(view, "Terbitkan Invoice")
    dialog = page.dialogs[-1]

    assert isinstance(dialog, ft.AlertDialog)
    assert saved(case) == draft

    action(dialog, "Batal")

    assert not dialog.open
    assert saved(case) == draft
    assert not find(
        view,
        ft.Button,
        "content",
        "Terbitkan Invoice",
    ).disabled


def test_dismiss_allows_opening_confirmation_again(case):
    _, draft, page, view = case

    click(view, "Terbitkan Invoice")
    dialog = page.pop_dialog()
    dialog.on_dismiss(SimpleNamespace(control=dialog))

    click(view, "Terbitkan Invoice")

    assert page.dialogs[-1] is not dialog
    assert saved(case) == draft


def test_publish_refreshes_detail_and_list(case):
    _, _, page, view = case

    click(view, "Terbitkan Invoice")
    dialog = page.dialogs[-1]
    action(dialog, "Terbitkan")

    assert not dialog.open
    assert saved(case)["document_status"] == "ISSUED"
    assert "INV-2026-0001" in texts(view)
    assert "Status: Diterbitkan" in texts(view)
    assert not has_button(view, "Terbitkan Invoice")
    assert "berhasil diterbitkan" in page.dialogs[-1].content.value

    click(view, "Kembali ke Daftar")

    assert find(
        view,
        ft.Dropdown,
        "label",
        "Status",
    ).value == "ISSUED"

    assert "INV-2026-0001" in texts(view)
    assert not has_button(view, "Edit Draft")


def test_duplicate_events_publish_only_once(case, monkeypatch):
    _, _, page, view = case
    original = invoice_service.publish_invoice
    calls = []

    def tracked(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(
        invoice_service,
        "publish_invoice",
        tracked,
    )

    button = find(
        view,
        ft.Button,
        "content",
        "Terbitkan Invoice",
    )
    event = SimpleNamespace(control=button)

    button.on_click(event)
    button.on_click(event)

    assert len(page.dialogs) == 1

    dialog = page.dialogs[-1]
    confirm = next(
        item
        for item in dialog.actions
        if item.content == "Terbitkan"
    )
    confirm_event = SimpleNamespace(control=confirm)

    confirm.on_click(confirm_event)
    confirm.on_click(confirm_event)

    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["validation", "storage"])
def test_publish_error_keeps_draft_and_allows_retry(
    case,
    monkeypatch,
    failure,
):
    _, draft, page, view = case

    def fail(*args):
        if failure == "validation":
            raise invoice_service.InvoiceValidationError(
                "Periksa data invoice."
            )

        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(invoice_service, "publish_invoice", fail)

        click(view, "Terbitkan Invoice")
        action(page.dialogs[-1], "Terbitkan")

    assert saved(case) == draft
    assert isinstance(page.dialogs[-1], ft.SnackBar)

    expected = (
        "Periksa data invoice."
        if failure == "validation"
        else "Invoice belum dapat diterbitkan. Silakan coba kembali."
    )
    assert page.dialogs[-1].content.value == expected

    assert not find(
        view,
        ft.Button,
        "content",
        "Terbitkan Invoice",
    ).disabled

    click(view, "Terbitkan Invoice")
    action(page.dialogs[-1], "Terbitkan")

    assert saved(case)["document_status"] == "ISSUED"


@pytest.mark.parametrize("status", ["ISSUED", "CANCELLED"])
def test_non_draft_detail_has_no_publish_button(case, status):
    path, draft, page, _ = case

    if status == "ISSUED":
        invoice_service.publish_invoice(path, draft["id"])
    else:
        with closing(connect(path)) as connection:
            connection.execute(
                """
                UPDATE invoices
                SET document_status = 'CANCELLED'
                WHERE id = ?
                """,
                (draft["id"],),
            )

    view = build_invoice_view(page, path)
    click(view, "Lihat Detail")

    assert not has_button(view, "Terbitkan Invoice")


def test_invoice_published_elsewhere_does_not_get_second_number(case):
    path, draft, page, view = case

    click(view, "Terbitkan Invoice")
    dialog = page.dialogs[-1]

    invoice_service.publish_invoice(path, draft["id"])
    action(dialog, "Terbitkan")

    assert isinstance(page.dialogs[-1], ft.SnackBar)

    with closing(connect(path)) as connection:
        assert connection.execute(
            "SELECT last_value FROM document_sequences "
            "WHERE document_type = 'INVOICE'"
        ).fetchone()[0] == 1