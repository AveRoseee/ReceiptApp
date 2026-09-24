from contextlib import closing
from types import SimpleNamespace

import flet as ft
import pytest

import main as application
from app.database import connect, initialize_database
from app.services import catalog_service, customer_service, quotation_service
from app.views.catalog_view import build_catalog_view
from app.views.quotation_view import build_quotation_view


class FakePage:
    """Exercise event handlers without starting the desktop client."""

    def __init__(self):
        self.controls = []
        self.dialogs = []
        self.services = []
        self.window = SimpleNamespace()

    def update(self):
        pass

    def add(self, *controls):
        self.controls.extend(controls)

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)


def walk(control):
    if not control.visible:
        return
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from walk(content)


def find(view, control_type, attribute, value):
    return next(
        control for control in walk(view)
        if isinstance(control, control_type)
        and getattr(control, attribute, None) == value
    )


def click(view, label):
    button = find(view, (ft.Button, ft.TextButton), "content", label)
    assert not button.disabled
    button.on_click(SimpleNamespace(control=button))


def field(view, label):
    return find(view, ft.TextField, "label", label)


def texts(view):
    return [
        control.value for control in walk(view)
        if isinstance(control, ft.Text)
    ]


@pytest.fixture
def database_path(tmp_path):
    return initialize_database(tmp_path / "views.db")


@pytest.fixture
def page():
    return FakePage()


def create_customer(database_path):
    with closing(connect(database_path)) as connection:
        return customer_service.create_customer(
            connection, {"name": "Pelanggan Awal"},
        )


def create_draft(database_path, customer_id):
    with closing(connect(database_path)) as connection:
        return quotation_service.create_draft(connection, {
            "customer_id": customer_id,
            "issue_date": "2026-09-23",
            "items": [{
                "name_snapshot": "Konsultasi",
                "unit": "jam",
                "quantity_milli": 1000,
                "unit_price": 100000,
            }],
        })


def test_catalog_edit_preserves_fields_and_new_form_resets(database_path, page):
    with closing(connect(database_path)) as connection:
        item = catalog_service.create_item(connection, {
            "name": "Konsultasi gratis",
            "type": "SERVICE",
            "unit": "jam",
            "default_price": 0,
            "description": "Sesi pengenalan",
        })

    view = build_catalog_view(page, database_path)
    click(view, "Edit")
    assert field(view, "Nama Produk/Jasa").value == "Konsultasi gratis"
    assert field(view, "Harga default (Rp) *").value == "0"
    assert field(view, "Satuan *").value == "jam"
    assert field(view, "Deskripsi").value == "Sesi pengenalan"
    assert find(view, ft.Dropdown, "label", "Jenis Item").value == "SERVICE"

    field(view, "Nama Produk/Jasa").value = "Konsultasi diperbarui"
    click(view, "Simpan Item")
    with closing(connect(database_path)) as connection:
        saved = catalog_service.get_item(connection, item["id"])
    assert saved["name"] == "Konsultasi diperbarui"
    assert saved["type"] == "SERVICE"
    assert saved["default_price"] == 0

    click(view, "Tambah Item")
    assert field(view, "Nama Produk/Jasa").value == ""
    assert field(view, "Satuan *").value == "pcs"
    assert field(view, "Harga default (Rp) *").value == "0"
    assert find(view, ft.Dropdown, "label", "Jenis Item").value == "PRODUCT"


def test_catalog_pagination_and_archived_filter(database_path, page):
    with closing(connect(database_path)) as connection:
        for index in range(21):
            catalog_service.create_item(connection, {
                "name": f"Produk {index:02}",
                "unit": "pcs",
                "default_price": 1000,
            })

    view = build_catalog_view(page, database_path)
    click(view, "Selanjutnya")
    assert "Produk 20" in texts(view)
    assert not find(view, ft.Button, "content", "Sebelumnya").disabled
    click(view, "Sebelumnya")
    assert "Produk 00" in texts(view)
    click(view, "Selanjutnya")
    click(view, "Arsipkan")
    assert "Produk 00" in texts(view)
    assert find(view, ft.Button, "content", "Sebelumnya").disabled

    checkbox = find(
        view, ft.Checkbox, "label",
        "Sertakan Produk/Jasa yang diarsipkan",
    )
    checkbox.value = True
    checkbox.on_change(SimpleNamespace(control=checkbox))
    search = field(view, "Cari Produk/Jasa")
    search.value = "Produk 20"
    search.on_submit(SimpleNamespace(control=search))
    click(view, "Aktifkan kembali")
    assert "Produk 20" in texts(view)
    with closing(connect(database_path)) as connection:
        active = catalog_service.list_items(connection)
    assert len(active) == 21


def test_quotation_form_saves_totals_and_rejects_invalid_update(database_path, page):
    customer = create_customer(database_path)
    view = build_quotation_view(page, database_path)
    click(view, "Tambah Penawaran")
    find(view, ft.Dropdown, "label", "Pelanggan").value = str(customer["id"])
    field(view, "Tanggal (YYYY-MM-DD)").value = "2026-09-23"
    click(view, "Tambah Item Manual")
    field(view, "Nama item").value = "Konsultasi"
    field(view, "Satuan").value = "jam"
    field(view, "Kuantitas").value = "1,5"
    field(view, "Harga satuan (Rp)").value = "100000"
    discount_type = find(view, ft.Dropdown, "label", "Jenis diskon")
    discount_type.value = "PERCENT"
    discount_type.on_select(SimpleNamespace(control=discount_type))
    field(view, "Diskon (%)").value = "10"
    tax = field(view, "Pajak (%)")
    tax.value = "11"
    tax.on_change(SimpleNamespace(control=tax))
    assert any("Total: Rp149.850" in (text or "") for text in texts(view))

    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        draft = quotation_service.list_quotations(connection)[0]
    assert draft["grand_total"] == 149850
    assert draft["status"] == "DRAFT"
    assert draft["number"] is None

    click(view, "Edit Draft")
    field(view, "Kuantitas").value = "0"
    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        saved = quotation_service.get_quotation(connection, draft["id"])
    assert saved["items"][0]["quantity_milli"] == 1500
    assert saved["grand_total"] == 149850

    field(view, "Kuantitas").value = "1,5"
    field(view, "Harga satuan (Rp)").value = "200000"
    click(view, "Simpan Draft")
    with closing(connect(database_path)) as connection:
        saved = quotation_service.get_quotation(connection, draft["id"])
    assert saved["grand_total"] == 299700
    click(view, "Lihat Detail")
    assert "Status: Draft" in texts(view)
    assert "Total: Rp299.700" in texts(view)


@pytest.mark.parametrize("failure", ["missing", "snapshot"])
def test_quotation_detail_errors_are_shown_to_user(
    database_path, page, monkeypatch, failure,
):
    customer = create_customer(database_path)
    create_draft(database_path, customer["id"])
    view = build_quotation_view(page, database_path)

    if failure == "missing":
        button = find(view, ft.TextButton, "content", "Lihat Detail")
        button.data = 999999
    else:
        monkeypatch.setattr(
            quotation_service, "get_quotation",
            lambda *args: {
                "status": "SENT",
                "business_snapshot": "invalid json",
            },
        )

    click(view, "Lihat Detail")
    assert page.dialogs
    message = page.dialogs[-1].content.value
    assert (
        "tidak ditemukan" in message.lower()
        if failure == "missing"
        else "snapshot" in message.lower()
    )
    assert find(view, ft.Button, "content", "Tambah Penawaran")


def test_navigation_refreshes_names_without_discarding_unsaved_form(
    database_path, page, monkeypatch,
):
    customer = create_customer(database_path)
    create_draft(database_path, customer["id"])
    monkeypatch.setattr(application, "initialize_database", lambda: database_path)
    application.main(page)
    view = page.controls[0]

    click(view, "Pelanggan")
    click(view, "Edit")
    field(view, "Nama pelanggan / kontak *").value = "Pelanggan Baru"
    click(view, "Simpan Pelanggan")
    click(view, "Penawaran")
    assert "Pelanggan Baru" in texts(view)
    assert "Pelanggan Awal" not in texts(view)

    click(view, "Edit Draft")
    field(view, "Catatan").value = "Belum disimpan"
    click(view, "Pelanggan")
    click(view, "Penawaran")
    assert field(view, "Catatan").value == "Belum disimpan"
