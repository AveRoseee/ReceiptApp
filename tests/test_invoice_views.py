from contextlib import closing
from types import SimpleNamespace

import flet as ft
import pytest

import main as application
from app.database import connect
from app.services import catalog_service, customer_service, invoice_service
from app.views.invoice_view import build_invoice_view

from test_view_interactions import (
    FakePage, click, create_customer, database_path, field, find, page, texts, walk,
)


def draft(database_path, customer_id):
    with closing(connect(database_path)) as connection:
        return invoice_service.create_draft(connection, {
            "customer_id": customer_id,
            "issue_date": "2026-09-23",
            "due_date": "2026-09-30",
        })


def test_invoice_menu_jastip_form_and_detail(database_path, page, monkeypatch):
    customer = create_customer(database_path)
    monkeypatch.setattr(application, "initialize_database", lambda: database_path)
    application.main(page)
    view = page.controls[0]
    click(view, "Invoice")
    click(view, "Tambah Invoice")
    find(view, ft.Dropdown, "label", "Pelanggan").value = str(customer["id"])
    field(view, "Tanggal (YYYY-MM-DD)").value = "2026-09-23"
    field(view, "Jatuh tempo (opsional, YYYY-MM-DD)").value = "2026-09-30"
    for name, price in [
        ("Action Figure", "150000"), ("Sepatu Gunung", "450000"),
        ("Jasa Titip", "100000"), ("Pengiriman", "25000"),
    ]:
        click(view, "Tambah Item Manual")
        controls = list(walk(view))
        def last_field(label):
            return [c for c in controls if isinstance(c, ft.TextField) and c.label == label][-1]
        last_field("Nama item").value = name
        last_field("Satuan").value = "pcs"
        price_field = last_field("Harga satuan (Rp)")
        price_field.value = price
        price_field.on_change(SimpleNamespace(control=price_field))
    assert any("Total: Rp725.000" in (value or "") for value in texts(view))
    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        invoices = invoice_service.list_invoices(connection)
        saved = invoice_service.get_invoice(connection, invoices[0]["id"])
        assert connection.execute("SELECT count(*) FROM quotations").fetchone()[0] == 0
    assert len(invoices) == 1
    assert saved["grand_total"] == 725000
    assert len(saved["items"]) == 4
    assert saved["quotation_id"] is None
    assert saved["number"] is None

    click(view, "Lihat Detail")
    assert "Status: Draft" in texts(view)
    assert "Jatuh tempo: 2026-09-30" in texts(view)
    assert "Total: Rp725.000" in texts(view)
    click(view, "Kembali ke Daftar")
    click(view, "Edit Draft")
    field(view, "Kuantitas").value = "0"
    click(view, "Simpan Draft")
    assert field(view, "Kuantitas").value == "0"
    with closing(connect(database_path)) as connection:
        assert invoice_service.get_invoice(connection, saved["id"])["grand_total"] == 725000
    field(view, "Kuantitas").value = "2"
    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        assert invoice_service.get_invoice(connection, saved["id"])["grand_total"] == 875000


def test_invoice_catalog_snapshot_and_cancel(database_path, page):
    customer = create_customer(database_path)
    with closing(connect(database_path)) as connection:
        item = catalog_service.create_item(connection, {
            "name": "Barang Awal", "unit": "pcs", "default_price": 150000,
        })
    view = build_invoice_view(page, database_path)
    click(view, "Tambah Invoice")
    find(view, ft.Dropdown, "label", "Pelanggan").value = str(customer["id"])
    find(view, ft.Dropdown, "label", "Item katalog").value = str(item["id"])
    click(view, "Tambah dari Katalog")
    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        catalog_service.update_item(connection, item["id"], {
            "name": "Barang Baru", "default_price": 900000,
        })
        before = invoice_service.get_invoice(connection, 1)
    click(view, "Edit Draft")
    assert field(view, "Nama item").value == "Barang Awal"
    assert field(view, "Harga satuan (Rp)").value == "150000"
    field(view, "Harga satuan (Rp)").value = "10"
    click(view, "Batal")
    with closing(connect(database_path)) as connection:
        assert invoice_service.get_invoice(connection, 1) == before


def test_invoice_pagination_refresh_and_status_filter(database_path, page):
    customer = create_customer(database_path)
    drafts = [draft(database_path, customer["id"]) for _ in range(21)]
    view = build_invoice_view(page, database_path)
    selector = find(view, ft.Dropdown, "label", "Status")
    selector.value = "DRAFT"
    selector.on_select(SimpleNamespace(control=selector))
    click(view, "Selanjutnya")
    assert not find(view, ft.TextButton, "content", "Sebelumnya").disabled
    # Another local operation removes the last page from the selected filter.
    with closing(connect(database_path)) as connection:
        connection.execute(
            "UPDATE invoices SET document_status = 'CANCELLED' WHERE id = ?",
            (drafts[0]["id"],),
        )
    view.data()
    assert find(view, ft.TextButton, "content", "Sebelumnya").disabled
    assert any("Halaman 1" in (value or "") for value in texts(view))
    selector.value = "CANCELLED"
    selector.on_select(SimpleNamespace(control=selector))
    assert not any(
        isinstance(c, ft.TextButton) and c.content == "Edit Draft"
        for c in walk(view)
    )
    click(view, "Lihat Detail")
    assert "Status: Dibatalkan" in texts(view)


def test_invoice_navigation_refresh_keeps_unsaved_form(database_path, page, monkeypatch):
    customer = create_customer(database_path)
    draft(database_path, customer["id"])
    monkeypatch.setattr(application, "initialize_database", lambda: database_path)
    application.main(page)
    view = page.controls[0]
    click(view, "Pelanggan")
    click(view, "Edit")
    field(view, "Nama pelanggan / kontak *").value = "Nama Terbaru"
    click(view, "Simpan Pelanggan")
    click(view, "Invoice")
    assert "Nama Terbaru" in texts(view)
    click(view, "Edit Draft")
    field(view, "Catatan").value = "Belum disimpan"
    click(view, "Katalog")
    click(view, "Invoice")
    assert field(view, "Catatan").value == "Belum disimpan"


@pytest.mark.parametrize("failure", ["missing", "snapshot"])
def test_invoice_detail_errors_do_not_crash(database_path, page, monkeypatch, failure):
    customer = create_customer(database_path)
    draft(database_path, customer["id"])
    view = build_invoice_view(page, database_path)
    if failure == "missing":
        find(view, ft.TextButton, "content", "Lihat Detail").data = 999
    else:
        monkeypatch.setattr(invoice_service, "get_invoice", lambda *args: {
            "number": "INV-TEST-0001",
            "document_status": "ISSUED",
            "business_snapshot": "invalid json",
        })
    click(view, "Lihat Detail")
    assert page.dialogs
    assert find(view, ft.Button, "content", "Tambah Invoice")
