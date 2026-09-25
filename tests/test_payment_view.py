from contextlib import closing
from types import SimpleNamespace
import sqlite3
from uuid import uuid4

import flet as ft
import pytest

from app.database import connect, initialize_database
from app.services import (
    customer_service,
    invoice_service,
    payment_service,
)
from app.services.business_profile_service import save_business_profile
from app.views.invoice_view import build_invoice_view
from app.views.payment_panel import PaymentPanel

from test_view_interactions import (
    FakePage,
    click,
    field,
    texts,
    walk,
)


class Page(FakePage):
    def show_dialog(self, dialog):
        dialog.open = True
        super().show_dialog(dialog)

    def pop_dialog(self):
        dialog = next(
            (
                dialog
                for dialog in reversed(self.dialogs)
                if dialog.open
            ),
            None,
        )

        if dialog is not None:
            dialog.open = False

        return dialog


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "payments-ui.db")

    with closing(connect(path)) as connection:
        save_business_profile(
            connection,
            {"name": "Jastip Jepang"},
        )

        customer = customer_service.create_customer(
            connection,
            {"name": "Pembeli"},
        )

        draft = invoice_service.create_draft(
            connection,
            {
                "customer_id": customer["id"],
                "issue_date": "2026-09-24",
                "items": [
                    {
                        "name_snapshot": "Titipan",
                        "unit": "paket",
                        "quantity_milli": 1000,
                        "unit_price": 725000,
                    }
                ],
            },
        )

    invoice_service.publish_invoice(path, draft["id"])

    page = Page()
    panel = PaymentPanel(page, path)
    panel.load(draft["id"])

    return SimpleNamespace(
        path=path,
        invoice_id=draft["id"],
        page=page,
        panel=panel,
    )


def fill(case, amount="300000"):
    click(case.panel.control, "Catat Pembayaran")

    field(
        case.panel.control,
        "Nominal pembayaran (Rp)",
    ).value = amount

    field(
        case.panel.control,
        "Tanggal pembayaran (YYYY-MM-DD)",
    ).value = "2026-09-24"


def save(case, amount="300000"):
    fill(case, amount)
    click(case.panel.control, "Simpan Pembayaran")


def summary(case):
    with closing(connect(case.path)) as connection:
        return payment_service.get_invoice_summary(
            connection,
            case.invoice_id,
        )


def payments(case):
    with closing(connect(case.path)) as connection:
        return payment_service.list_payments(
            connection,
            case.invoice_id,
        )


def dialog_click(dialog, label):
    button = next(
        button
        for button in dialog.actions
        if button.content == label
    )

    assert not button.disabled
    button.on_click(SimpleNamespace(control=button))


def button_visible(root, label):
    return any(
        isinstance(control, (ft.Button, ft.TextButton))
        and control.content == label
        for control in walk(root)
    )


def assert_error(panel):
    assert isinstance(panel.form_error.value, str)
    assert panel.form_error.value.strip()


def test_invoice_detail_memuat_panel_dan_pembayaran(case):
    view = build_invoice_view(case.page, case.path)

    click(view, "Lihat Detail")
    click(view, "Catat Pembayaran")

    field(
        view,
        "Nominal pembayaran (Rp)",
    ).value = "300000"

    click(view, "Simpan Pembayaran")

    assert summary(case)["balance_due"] == 425000
    assert any(
        "Rp425.000" in (value or "")
        for value in texts(view)
    )

    click(view, "Kembali ke Daftar")
    click(view, "Lihat Detail")

    assert any(
        "Rp425.000" in (value or "")
        for value in texts(view)
    )


def test_dp_cicilan_pelunasan_dan_buka_ulang(case):
    save(case, "300000")

    assert summary(case)["settlement_status"] == "PARTIAL"
    assert "Rp425.000" in case.panel.balance.value

    save(case, "200000")
    save(case, "225000")

    assert summary(case)["settlement_status"] == "PAID"
    assert not button_visible(
        case.panel.control,
        "Catat Pembayaran",
    )

    reopened = PaymentPanel(Page(), case.path)
    reopened.load(case.invoice_id)

    assert reopened.summary["paid_amount"] == 725000
    assert len(payments(case)) == 3


@pytest.mark.parametrize(
    "amount",
    ["", "0", "-1", "300.000", "1,5", "Rp300000", "725001"],
)
def test_nominal_salah_bisa_diperbaiki(case, amount):
    fill(case, amount)
    click(case.panel.control, "Simpan Pembayaran")

    assert payments(case) == []
    assert case.panel.amount.value == amount
    assert case.panel.form.visible
    assert_error(case.panel)
    assert not case.panel.save_button.disabled

    case.panel.amount.value = "300000"
    click(case.panel.control, "Simpan Pembayaran")

    assert summary(case)["balance_due"] == 425000


@pytest.mark.parametrize(
    "method",
    ["CASH", "TRANSFER", "QRIS", "OTHER"],
)
def test_metode_referensi_dan_catatan(case, method):
    fill(case)

    case.panel.method.value = method
    case.panel.reference.value = "TRX-001"
    case.panel.notes.value = "DP dari pembeli"

    click(case.panel.control, "Simpan Pembayaran")

    result = payments(case)[0]

    assert result["method"] == method
    assert result["reference"] == "TRX-001"
    assert result["notes"] == "DP dari pembeli"


def test_tanggal_salah_dan_tutup_form_tidak_menyimpan(case):
    fill(case)

    case.panel.payment_date.value = "24/09/2026"
    click(case.panel.control, "Simpan Pembayaran")

    assert_error(case.panel)
    assert payments(case) == []

    click(case.panel.control, "Tutup Form Pembayaran")

    assert not case.panel.form.visible
    assert payments(case) == []


def test_klik_simpan_berulang_tidak_menggandakan(case):
    fill(case)

    button = case.panel.save_button
    event = SimpleNamespace(control=button)

    button.on_click(event)
    button.on_click(event)

    assert len(payments(case)) == 1
    assert summary(case)["paid_amount"] == 300000


@pytest.mark.parametrize("committed", [False, True])
def test_coba_ulang_setelah_gagal_tidak_menggandakan(
    case,
    monkeypatch,
    committed,
):
    original = payment_service.record_payment
    seen = []

    def uncertain(
        connection,
        invoice_id,
        data,
        *,
        request_id,
    ):
        seen.append(request_id)

        if committed:
            original(
                connection,
                invoice_id,
                data,
                request_id=request_id,
            )

        raise sqlite3.OperationalError("Simulasi gangguan")

    fill(case, "725000")

    with monkeypatch.context() as patch:
        patch.setattr(
            payment_service,
            "record_payment",
            uncertain,
        )
        click(case.panel.control, "Simpan Pembayaran")

    assert_error(case.panel)
    assert case.panel.amount.disabled
    assert not case.panel.save_button.disabled

    # Menutup dan membuka ulang form tidak membuat request_id baru.
    click(case.panel.control, "Tutup Form Pembayaran")
    click(case.panel.control, "Muat Ulang Pembayaran")
    click(case.panel.control, "Catat Pembayaran")

    assert case.panel.request_id == seen[0]

    click(case.panel.control, "Simpan Pembayaran")

    assert len(payments(case)) == 1
    assert summary(case)["balance_due"] == 0


def test_validasi_alasan_batal_dialog_dan_batalkan_pembayaran(case):
    save(case)

    payment = payments(case)[0]

    with closing(connect(case.path)) as connection:
        connection.execute(
            """
            INSERT INTO receipts (
                payment_id,
                number,
                issued_date,
                document_snapshot
            )
            VALUES (?, 'RCPT-TEST-1', '2026-09-24', '{}')
            """,
            (payment["id"],),
        )

    click(case.panel.control, "Batalkan Pembayaran")
    dialog = case.page.dialogs[-1]
    dialog_click(dialog, "Kembali")

    assert payments(case)[0]["status"] == "VALID"

    click(case.panel.control, "Batalkan Pembayaran")
    dialog = case.page.dialogs[-1]
    dialog_click(dialog, "Ya, Batalkan Pembayaran")

    reason = field(
        dialog.content,
        "Alasan pembatalan",
    )

    assert reason.error
    assert payments(case)[0]["status"] == "VALID"

    reason.value = "Salah nominal"

    confirm = next(
        button
        for button in dialog.actions
        if button.content == "Ya, Batalkan Pembayaran"
    )
    event = SimpleNamespace(control=confirm)

    confirm.on_click(event)
    confirm.on_click(event)

    assert not dialog.open
    assert payments(case)[0]["status"] == "VOID"
    assert payments(case)[0]["void_reason"] == "Salah nominal"
    assert summary(case)["balance_due"] == 725000

    with closing(connect(case.path)) as connection:
        assert connection.execute(
            "SELECT status FROM receipts"
        ).fetchone()[0] == "VOID"

    assert not button_visible(
        case.panel.control,
        "Batalkan Pembayaran",
    )

    case.panel.include_void.value = False
    case.panel.include_void.on_change(
        SimpleNamespace(control=case.panel.include_void)
    )

    assert not button_visible(
        case.panel.control,
        "Batalkan Pembayaran",
    )
    assert isinstance(case.panel.history.controls[0], ft.Text)


def test_gagal_pembatalan_tetap_mempertahankan_data(
    case,
    monkeypatch,
):
    save(case)

    click(case.panel.control, "Batalkan Pembayaran")
    dialog = case.page.dialogs[-1]

    field(
        dialog.content,
        "Alasan pembatalan",
    ).value = "Keliru"

    def fail(*args):
        raise sqlite3.OperationalError("Simulasi gagal")

    with monkeypatch.context() as patch:
        patch.setattr(
            payment_service,
            "void_payment",
            fail,
        )
        dialog_click(dialog, "Ya, Batalkan Pembayaran")

    assert dialog.open
    assert payments(case)[0]["status"] == "VALID"
    assert any(
        isinstance(control, ft.Text)
        and control.color == ft.Colors.RED_700
        and control.value
        for control in walk(dialog.content)
    )

    dialog_click(dialog, "Ya, Batalkan Pembayaran")

    assert payments(case)[0]["status"] == "VOID"


def test_riwayat_paginasi(case):
    with closing(connect(case.path)) as connection:
        for _ in range(21):
            payment_service.record_payment(
                connection,
                case.invoice_id,
                {
                    "payment_date": "2026-09-24",
                    "amount": 1000,
                    "method": "CASH",
                },
                request_id=str(uuid4()),
            )

    case.panel.refresh()

    assert len(case.panel.history.controls) == 20

    click(case.panel.control, "Riwayat berikutnya")

    assert len(case.panel.history.controls) == 1
    assert case.panel.next.disabled

    click(case.panel.control, "Riwayat sebelumnya")

    assert case.panel.previous.disabled


def test_gagal_memuat_bisa_dicoba_lagi(case, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("Simulasi gagal baca")

    with monkeypatch.context() as patch:
        patch.setattr(
            payment_service,
            "get_invoice_summary",
            fail,
        )
        case.panel.refresh()

    assert case.panel.load_error.value
    assert case.panel.balance.value == ""
    assert case.panel.add_button.disabled

    click(case.panel.control, "Muat Ulang Pembayaran")

    assert case.panel.load_error.value == ""
    assert not case.panel.add_button.disabled


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED"])
def test_invoice_draft_dan_batal_tidak_bisa_mencatat(case, status):
    with closing(connect(case.path)) as connection:
        original = invoice_service.get_invoice(
            connection,
            case.invoice_id,
        )

        other = invoice_service.create_draft(
            connection,
            {
                "customer_id": original["customer_id"],
                "issue_date": "2026-09-24",
            },
        )

        if status == "CANCELLED":
            connection.execute(
                """
                UPDATE invoices
                SET document_status = 'CANCELLED'
                WHERE id = ?
                """,
                (other["id"],),
            )

    case.panel.load(other["id"])

    assert not button_visible(
        case.panel.control,
        "Catat Pembayaran",
    )


def test_validasi_saldo_terbaru_saat_form_masih_terbuka(case):
    fill(case, "725000")

    with closing(connect(case.path)) as connection:
        payment_service.record_payment(
            connection,
            case.invoice_id,
            {
                "payment_date": "2026-09-24",
                "amount": 300000,
                "method": "CASH",
            },
            request_id=str(uuid4()),
        )

    click(case.panel.control, "Simpan Pembayaran")

    assert_error(case.panel)
    assert len(payments(case)) == 1

    case.panel.amount.value = "425000"
    click(case.panel.control, "Simpan Pembayaran")

    assert summary(case)["balance_due"] == 0