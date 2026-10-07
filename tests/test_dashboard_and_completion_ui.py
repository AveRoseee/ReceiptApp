import asyncio
from contextlib import closing
from datetime import date, timedelta
from types import SimpleNamespace
from io import BytesIO

import flet as ft
from pypdf import PdfReader
import pytest

import main
from app.database import connect
from app.services import dashboard_service, invoice_service, backup_service
from app.services.business_profile_service import save_business_profile, get_business_profile
from app.views.invoice_view import build_invoice_view
from app.views.settings_view import build_settings_view
from test_core_invoice import business, record
from test_view_interactions import FakePage, click, find, field, walk


def test_dashboard_excludes_void_and_cancelled_and_has_no_float(business):
    path, invoice = business
    with closing(connect(path)) as db:
        initial = dashboard_service.get_dashboard(db)
        assert initial["outstanding"] == 725000
        payment = record(db, invoice)
        data = dashboard_service.get_dashboard(db)
        assert data["outstanding"] == 425000
        assert type(data["month_received"]) is int
        from app.services.payment_service import void_payment
        void_payment(db, payment["id"], "Salah input")
        invoice_service.cancel_invoice(db, invoice["id"], "Salah input")
        assert dashboard_service.get_dashboard(db)["outstanding"] == 0


def test_invoice_filters_dates_and_payment_status(business):
    path, invoice = business
    with closing(connect(path)) as db:
        assert len(invoice_service.list_invoices(db, payment_status="UNPAID")) == 1
        record(db, invoice)
        assert invoice_service.list_invoices(db, payment_status="PAID") == []
        assert len(invoice_service.list_invoices(db, payment_status="PARTIAL")) == 1
        assert invoice_service.list_invoices(db, date_from="2026-09-30") == []
        assert len(invoice_service.list_invoices(db, date_from="2026-09-29", date_to="2026-09-29")) == 1
        with pytest.raises(invoice_service.InvoiceValidationError):
            invoice_service.list_invoices(db, date_from="2026-10-01", date_to="2026-09-01")


def test_dashboard_due_date(business):
    path, invoice = business
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    with closing(connect(path)) as db:
        draft = invoice_service.duplicate_as_draft(db, invoice["id"], issue_date=yesterday)
        invoice_service.update_draft(db, draft["id"], {"due_date": yesterday})
    invoice_service.publish_invoice(path, draft["id"])
    with closing(connect(path)) as db:
        data = dashboard_service.get_dashboard(db)
        assert data["overdue_count"] == 1
        assert data["due_invoices"][0]["id"] == draft["id"]
        assert len(invoice_service.list_invoices(db, payment_status="OVERDUE")) == 1


def test_dashboard_month_excludes_adjacent_months_and_void(business, monkeypatch):
    from uuid import uuid4
    from app.services.payment_service import record_payment, void_payment
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 29)
    monkeypatch.setattr(dashboard_service, "date", Today)
    path, invoice = business
    with closing(connect(path)) as db:
        draft = invoice_service.duplicate_as_draft(db, invoice["id"], issue_date="2026-08-01")
    issued = invoice_service.publish_invoice(path, draft["id"])
    with closing(connect(path)) as db:
        payments = []
        for day, amount in [("2026-08-31", 100), ("2026-09-01", 200),
                            ("2026-09-30", 300), ("2026-10-01", 400)]:
            payments.append(record_payment(db, issued["id"], {
                "payment_date": day, "amount": amount, "method": "CASH",
            }, request_id=str(uuid4())))
        assert dashboard_service.get_dashboard(db)["month_received"] == 500
        void_payment(db, payments[1]["id"], "Salah input")
        assert dashboard_service.get_dashboard(db)["month_received"] == 300


def test_invoice_filter_validation_is_visible_and_can_be_corrected(business):
    path, _ = business
    page = FakePage()
    view = build_invoice_view(page, path)
    field(view, "Tanggal awal (YYYY-MM-DD)").value = "2026-10-01"
    field(view, "Tanggal akhir (YYYY-MM-DD)").value = "2026-09-01"
    click(view, "Cari")
    with closing(connect(path)) as db:
        with pytest.raises(invoice_service.InvoiceValidationError) as error:
            invoice_service.list_invoices(db, date_from="2026-10-01", date_to="2026-09-01")
    assert find(view, ft.Text, "value", str(error.value)).visible
    field(view, "Tanggal awal (YYYY-MM-DD)").value = "2026-09-01"
    field(view, "Tanggal akhir (YYYY-MM-DD)").value = "2026-09-30"
    click(view, "Cari")
    assert find(view, ft.TextButton, "content", "Lihat Detail").visible


def test_main_dashboard_shortcuts(business, monkeypatch):
    path, _ = business
    monkeypatch.setattr(main, "initialize_database", lambda: path)
    page = FakePage()
    main.main(page)
    root = page.controls[0]
    click(root, "Dashboard")
    click(root, "Buat Invoice")
    assert find(root, ft.Button, "content", "Simpan Draft").visible
    click(root, "Dashboard")
    click(root, "Tambah Pelanggan")
    assert field(root, "Nama pelanggan / kontak *").visible


def test_receipt_ui_issues_and_exports_payment_snapshot(business, monkeypatch):
    path, invoice = business
    with closing(connect(path)) as db:
        record(db, invoice)
    page = FakePage()
    page.pop_dialog = lambda: None
    view = build_invoice_view(page, path)
    click(view, "Lihat Detail")
    click(view, "Buat Kwitansi")
    dialog = page.dialogs[-1]
    assert dialog.title.value.startswith("RCPT-")
    calls = []
    async def save(self, **kwargs):
        calls.append(kwargs)
        return "receipt.pdf"
    monkeypatch.setattr(ft.FilePicker, "save_file", save)
    button = find(dialog.content, ft.Button, "content", "Simpan PDF")
    asyncio.run(button.on_click(SimpleNamespace(control=button)))
    text = PdfReader(BytesIO(calls[0]["src_bytes"])).pages[0].extract_text()
    assert "Tiga ratus ribu rupiah" in text
    assert find(view, ft.TextButton, "content", "Lihat Kwitansi").visible


def test_restore_ui_requires_explicit_confirmation(business, monkeypatch):
    path, _ = business
    archive = backup_service.create_backup(path, path.parent / "backup.zip")
    with closing(connect(path)) as db:
        save_business_profile(db, {"name": "Changed"})
    page = FakePage()
    page.pop_dialog = lambda: None
    restored = []
    view = build_settings_view(page, path, lambda: restored.append(True))
    page.controls.append(view)
    async def pick(self, **kwargs):
        return [SimpleNamespace(path=str(archive))]
    monkeypatch.setattr(ft.FilePicker, "pick_files", pick)
    button = find(view, ft.Button, "content", "Pulihkan dari Cadangan")
    asyncio.run(button.on_click(None))
    dialog = page.dialogs[-1]
    dialog.actions[0].on_click(None)
    with closing(connect(path)) as db:
        assert get_business_profile(db)["name"] == "Changed"
    assert not restored
    asyncio.run(button.on_click(None))
    dialog = page.dialogs[-1]
    asyncio.run(dialog.actions[1].on_click(None))
    assert restored == [True]
    with closing(connect(path)) as db:
        assert get_business_profile(db)["name"] == "Usaha Awal"
