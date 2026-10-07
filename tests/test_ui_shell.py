from types import SimpleNamespace
import flet as ft
import main
from test_core_invoice import business
from test_view_interactions import FakePage, click, field, find


def test_resize_preserves_unsaved_invoice_and_navigation(business, monkeypatch):
    path, _ = business
    monkeypatch.setattr(main, "initialize_database", lambda: path)
    page = FakePage()
    page.width = 1280
    main.main(page)
    root = page.controls[0]
    click(root, "Invoice")
    click(root, "Tambah Invoice")
    field(root, "Catatan").value = "Belum disimpan"
    for width in (390, 1280):
        page.width = width
        page.on_resize(SimpleNamespace())
        assert field(root, "Catatan").value == "Belum disimpan"
        click(root, "Pelanggan")
        click(root, "Invoice")
        assert field(root, "Catatan").value == "Belum disimpan"
        assert find(root, ft.Button, "content", "Simpan Draft").visible
