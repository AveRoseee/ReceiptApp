"""Skenario pengguna untuk seluruh halaman DokumenUsaha.

Jalankan dengan pytest. Semua data memakai tmp_path, bukan database pengguna.
Tes memanggil handler Flet; tidak membuka atau menguji rendering klien native.
"""
import asyncio
from contextlib import closing
import inspect
import sqlite3
from types import SimpleNamespace

import flet as ft
from PIL import Image
import pytest

import main as application
from app.database import connect, initialize_database
from app.services import catalog_service, customer_service
from app.services import invoice_service, quotation_service
from app.services.business_profile_service import get_business_profile
from app.services.business_image_service import load_business_image

DOCUMENTS = {
    "Invoice": (invoice_service, "get_invoice", "invoices"),
    "Penawaran": (quotation_service, "get_quotation", "quotations"),
}
IMAGES = [
    ("Logo", "logo"), ("QRIS", "qris"),
    ("Tanda Tangan", "signature"), ("Stempel", "stamp"),
]


def emit(handler, control):
    if handler is None:
        return
    result = handler(SimpleNamespace(control=control, data=control.value
                                    if hasattr(control, "value") else None))
    if inspect.isawaitable(result):
        asyncio.run(result)


def walk(control):
    if control.visible is False:
        return
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from walk(content)
    for child in getattr(control, "actions", []) or []:
        yield from walk(child)


class Page:
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
        dialog.open = True
        self.dialogs.append(dialog)

    def pop_dialog(self):
        dialog = next(
            (d for d in reversed(self.dialogs) if d.open), None
        )
        if dialog:
            dialog.open = False
        return dialog


class User:
    def __init__(self, page, path):
        self.page = page
        self.path = path
        self.root = page.controls[0]

    def find(self, kind, attribute, value, index=0, root=None):
        matches = [
            c for c in walk(root if root is not None else self.root)
            if isinstance(c, kind) and getattr(c, attribute, None) == value
        ]
        assert matches, f"Kontrol tidak terlihat: {attribute}={value!r}"
        return matches[index]

    def button(self, label, root=None):
        return self.find((ft.Button, ft.TextButton), "content", label, root=root)

    def click(self, label, root=None):
        button = self.button(label, root)
        assert not button.disabled, f"Tombol nonaktif: {label}"
        emit(button.on_click, button)

    def go(self, label):
        button = self.button(label)
        if not button.disabled:
            emit(button.on_click, button)

    def field(self, label, index=0):
        return self.find((ft.TextField, ft.Dropdown, ft.Checkbox),
                         "label", label, index)

    def enter(self, label, value, index=0):
        control = self.field(label, index)
        assert not control.disabled
        control.value = value
        handler = (control.on_select if isinstance(control, ft.Dropdown)
                   else control.on_change)
        emit(handler, control)
        return control

    def text(self):
        return "\n".join(str(c.value or "") for c in walk(self.root)
                         if isinstance(c, ft.Text))

    def rows(self, table):
        # Nama tabel hanya berasal dari konstanta tes di file ini.
        with closing(connect(self.path)) as connection:
            return [dict(r) for r in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            )]

    def profile(self):
        self.go("Profil Usaha")
        self.enter("Nama Usaha *", "Jastip Jepang")
        self.click("Simpan Profil")

    def customer(self):
        self.go("Pelanggan")
        self.click("Tambah Pelanggan")
        self.enter("Nama pelanggan / kontak *", "Pelanggan Uji")
        self.click("Simpan Pelanggan")
        return self.rows("customers")[-1]["id"]

    def catalog(self):
        self.go("Katalog")
        self.click("Tambah Item")
        self.enter("Nama Produk/Jasa", "Action Figure")
        self.enter("Satuan *", "pcs")
        self.enter("Harga default (Rp) *", "300000")
        self.click("Simpan Item")
        return self.rows("catalog_items")[-1]["id"]

    def new_document(self, kind, customer_id):
        self.go(kind)
        self.click(f"Tambah {kind}")
        self.enter("Pelanggan", str(customer_id))
        self.enter("Tanggal (YYYY-MM-DD)", "2026-09-24")

    def manual(self, name="Jasa Titip", price="100000", quantity="1"):
        self.click("Tambah Item Manual")
        self.enter("Nama item", name, -1)
        self.enter("Satuan", "pcs", -1)
        self.enter("Kuantitas", quantity, -1)
        self.enter("Harga satuan (Rp)", price, -1)

    def document(self, kind):
        service, getter, table = DOCUMENTS[kind]
        document_id = self.rows(table)[-1]["id"]
        with closing(connect(self.path)) as connection:
            return getattr(service, getter)(connection, document_id)

    def confirm(self, label):
        dialog = next(
            d for d in reversed(self.page.dialogs)
            if isinstance(d, ft.AlertDialog) and d.open
        )
        self.click(label, root=dialog)
        return dialog


@pytest.fixture
def user(tmp_path, monkeypatch):
    path = initialize_database(tmp_path / "journeys.db")
    monkeypatch.setattr(application, "initialize_database", lambda: path)
    page = Page()
    application.main(page)
    assert page.controls, "Aplikasi gagal membuat halaman."
    return User(page, path)


def test_semua_menu_bisa_dibuka_saat_data_kosong(user):
    for menu in ("Pelanggan", "Katalog", "Invoice", "Penawaran", "Profil Usaha"):
        user.go(menu)
        assert user.button(menu).disabled
    assert user.rows("customers") == []
    assert user.rows("invoices") == []


def test_profil_kosong_ditolak_lalu_dapat_diperbaiki(user):
    user.click("Simpan Profil")
    assert user.field("Nama Usaha *").error
    with closing(connect(user.path)) as connection:
        assert get_business_profile(connection) is None
    user.enter("Nama Usaha *", "  Jastip Jepang  ")
    user.enter("Nomor Rekening", "0012345678")
    user.click("Simpan Profil")
    page = Page()
    application.main(page)
    reopened = User(page, user.path)
    assert reopened.field("Nama Usaha *").value == "Jastip Jepang"
    assert reopened.field("Nomor Rekening").value == "0012345678"


@pytest.mark.parametrize("label,image_type", IMAGES)
@pytest.mark.parametrize("selection", ["cancel", "invalid", "valid", "no_profile"])
def test_gambar_batal_file_salah_dan_tersimpan(
    user, tmp_path, monkeypatch, label, image_type, selection,
):
    if selection != "no_profile":
        user.profile()
    source = tmp_path / "gambar.png"
    if selection == "invalid":
        source.write_bytes(b"Ini bukan gambar.")
    else:
        Image.new("RGB", (16, 16), "blue").save(source)

    async def pick_files(self, **kwargs):
        return [] if selection == "cancel" else [
            SimpleNamespace(path=str(source))
        ]

    monkeypatch.setattr(ft.FilePicker, "pick_files", pick_files)
    user.click(f"Pilih & Simpan {label}")
    assert not user.button(f"Pilih & Simpan {label}").disabled
    data = load_business_image(user.path, image_type)
    if selection == "valid":
        assert data == source.read_bytes()
        page = Page()
        application.main(page)
        previews = [c for c in walk(page.controls[0]) if isinstance(c, ft.Image)]
        assert any(c.src == data for c in previews)
    else:
        assert data is None
        if selection in {"invalid", "no_profile"}:
            assert any(isinstance(c, ft.Text) and c.color == ft.Colors.RED_700
                       and c.value for c in walk(user.root))


@pytest.mark.parametrize("kind", ["Pelanggan", "Katalog"])
def test_master_wajib_diisi_edit_batal_cari_arsip_aktifkan(user, kind):
    customer = kind == "Pelanggan"
    table = "customers" if customer else "catalog_items"
    add = "Tambah Pelanggan" if customer else "Tambah Item"
    save = "Simpan Pelanggan" if customer else "Simpan Item"
    name = "Nama pelanggan / kontak *" if customer else "Nama Produk/Jasa"
    search = "Cari pelanggan" if customer else "Cari Produk/Jasa"
    archive = ("Sertakan pelanggan diarsipkan" if customer
               else "Sertakan Produk/Jasa yang diarsipkan")
    user.go(kind)
    user.click(add)
    user.click(save)
    assert user.rows(table) == []
    assert user.field(name).error

    user.enter(name, "Data Awal")
    if not customer:
        user.enter("Satuan *", "pcs")
        user.enter("Harga default (Rp) *", "100000")
    user.click(save)
    user.click("Edit")
    user.enter(name, "Jangan Disimpan")
    user.click("Batal")
    assert user.rows(table)[0]["name"] == "Data Awal"

    user.click("Edit")
    user.enter(name, "Data Baru")
    user.click(save)
    assert user.rows(table)[0]["name"] == "Data Baru"
    control = user.enter(search, "TIDAK_ADA_123")
    emit(control.on_submit, control)
    assert "Data Baru" not in user.text()
    control = user.enter(search, "")
    emit(control.on_submit, control)
    user.click("Arsipkan")
    assert user.rows(table)[0]["is_active"] == 0
    assert "Data Baru" not in user.text()
    user.enter(archive, True)
    assert "Data Baru" in user.text()
    user.click("Aktifkan Kembali" if customer else "Aktifkan kembali")
    assert user.rows(table)[0]["is_active"] == 1


@pytest.mark.parametrize("price", ["300.000", "Rp300000", "-1", "", "1,5"])
def test_katalog_harga_salah_ditolak_tanpa_kehilangan_isian(user, price):
    user.go("Katalog")
    user.click("Tambah Item")
    user.enter("Nama Produk/Jasa", "Sepatu")
    user.enter("Satuan *", "pcs")
    user.enter("Harga default (Rp) *", price)
    user.click("Simpan Item")
    assert user.rows("catalog_items") == []
    assert user.field("Harga default (Rp) *").error
    assert user.field("Nama Produk/Jasa").value == "Sepatu"
    user.enter("Harga default (Rp) *", "450000")
    user.click("Simpan Item")
    assert user.rows("catalog_items")[0]["default_price"] == 450000


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_pilih_katalog_belum_menambahkan_item(user, kind):
    customer_id = user.customer()
    catalog_id = user.catalog()
    user.new_document(kind, customer_id)
    user.click("Tambah dari Katalog")
    assert "Pilih item katalog" in user.text()
    user.enter("Item katalog", str(catalog_id))
    user.click("Simpan Draft")
    draft = user.document(kind)
    assert draft["items"] == []
    assert draft["grand_total"] == 0

    user.click("Edit Draft")
    user.enter("Item katalog", str(catalog_id))
    user.click("Tambah dari Katalog")
    assert user.field("Harga satuan (Rp)").value == "300000"
    assert "Total: Rp300.000" in user.text()
    user.click("Simpan Draft")
    assert user.document(kind)["grand_total"] == 300000
    user.click("Lihat Detail")
    assert "Total: Rp300.000" in user.text()


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_dokumen_tanpa_pelanggan_ditolak(user, kind):
    user.go(kind)
    user.click(f"Tambah {kind}")
    user.manual()
    user.click("Simpan Draft")
    assert "Pilih pelanggan terlebih dahulu" in user.text()
    assert user.rows(DOCUMENTS[kind][2]) == []
    assert user.field("Nama item").value == "Jasa Titip"


@pytest.mark.parametrize("kind", DOCUMENTS)
@pytest.mark.parametrize("label,value", [
    ("Kuantitas", "0"),
    ("Kuantitas", "-1"),
    ("Kuantitas", "1.5"),
    ("Harga satuan (Rp)", "300.000"),
    ("Harga satuan (Rp)", "Rp300000"),
    ("Pajak (%)", "101"),
    ("Diskon (Rp)", "999999"),
    ("Tanggal (YYYY-MM-DD)", "24/09/2026"),
])
def test_input_dokumen_salah_tidak_mengubah_draft(user, kind, label, value):
    customer_id = user.customer()
    user.new_document(kind, customer_id)
    user.manual()
    user.click("Simpan Draft")
    before = user.document(kind)
    user.click("Edit Draft")
    user.enter(label, value)
    user.click("Simpan Draft")
    assert user.document(kind) == before
    assert user.field(label).value == value
    assert not user.button("Simpan Draft").disabled


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_desimal_diskon_pajak_hapus_item_dan_batal(user, kind):
    customer_id = user.customer()
    user.new_document(kind, customer_id)
    user.manual(quantity="1,5")
    user.enter("Jenis diskon", "PERCENT")
    user.enter("Diskon (%)", "10")
    user.enter("Pajak (%)", "11")
    assert "Total: Rp149.850" in user.text()
    user.click("Simpan Draft")
    before = user.document(kind)
    assert before["grand_total"] == 149850
    user.click("Edit Draft")
    user.enter("Harga satuan (Rp)", "900000")
    user.click("Batal")
    assert user.document(kind) == before
    user.click("Edit Draft")
    user.click("Hapus Item")
    assert "Total: Rp0" in user.text()
    user.click("Simpan Draft")
    assert user.document(kind)["items"] == []


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_pindah_menu_mempertahankan_isian_belum_disimpan(user, kind):
    customer_id = user.customer()
    user.new_document(kind, customer_id)
    user.manual()
    user.enter("Catatan", "Belum selesai diisi")
    user.go("Katalog")
    user.go(kind)
    assert user.field("Catatan").value == "Belum selesai diisi"
    assert user.field("Nama item").value == "Jasa Titip"
    user.click("Simpan Draft")
    assert user.document(kind)["notes"] == "Belum selesai diisi"


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_jatuh_tempo_sebelum_tanggal_ditolak(user, kind):
    customer_id = user.customer()
    user.new_document(kind, customer_id)
    user.manual()
    label = ("Jatuh tempo (opsional, YYYY-MM-DD)" if kind == "Invoice"
             else "Berlaku sampai (opsional)")
    user.enter(label, "2026-09-23")
    user.click("Simpan Draft")
    assert user.rows(DOCUMENTS[kind][2]) == []
    assert "mendahului" in user.text().lower()


def test_alur_jastip_dari_awal_sampai_invoice_diterbitkan(user):
    user.profile()
    customer_id = user.customer()
    catalog_id = user.catalog()
    user.new_document("Invoice", customer_id)
    user.enter("Item katalog", str(catalog_id))
    user.click("Tambah dari Katalog")
    user.manual("Jasa Titip", "100000")
    user.manual("Pengiriman", "25000")
    user.click("Simpan Draft")
    assert user.document("Invoice")["grand_total"] == 425000
    user.click("Lihat Detail")
    user.click("Terbitkan Invoice")
    user.confirm("Batal")
    assert user.document("Invoice")["number"] is None
    user.click("Terbitkan Invoice")
    user.confirm("Terbitkan")
    issued = user.document("Invoice")
    assert issued["document_status"] == "ISSUED"
    assert issued["number"] == "INV-2026-0001"
    assert "Status: Diterbitkan" in user.text()
    user.click("Kembali ke Daftar")
    assert user.field("Status").value == "ISSUED"
    assert not any(isinstance(c, (ft.Button, ft.TextButton))
                   and c.content == "Edit Draft" for c in walk(user.root))


@pytest.mark.parametrize("reason", ["empty", "profile_missing"])
def test_invoice_belum_lengkap_tidak_bisa_diterbitkan(user, reason):
    customer_id = user.customer()
    if reason == "empty":
        user.profile()
    user.new_document("Invoice", customer_id)
    if reason != "empty":
        user.manual()
    user.click("Simpan Draft")
    before = user.document("Invoice")
    user.click("Lihat Detail")
    user.click("Terbitkan Invoice")
    user.confirm("Terbitkan")
    assert user.document("Invoice") == before
    assert isinstance(user.page.dialogs[-1], ft.SnackBar)
    assert not user.button("Terbitkan Invoice").disabled


@pytest.mark.parametrize("kind", ["Pelanggan", "Katalog", "Invoice", "Penawaran"])
def test_daftar_pencarian_dan_paginasi(user, kind):
    with closing(connect(user.path)) as connection:
        customer = customer_service.create_customer(connection, {"name": "Pembeli"})
        for index in range(21):
            if kind == "Pelanggan":
                customer_service.create_customer(connection, {"name": f"Orang {index:02}"})
            elif kind == "Katalog":
                catalog_service.create_item(connection, {
                    "name": f"Barang {index:02}", "unit": "pcs", "default_price": 1000,
                })
            else:
                DOCUMENTS[kind][0].create_draft(connection, {
                    "customer_id": customer["id"], "issue_date": "2026-09-24",
                })
    page = Page()
    application.main(page)
    user = User(page, user.path)
    user.go(kind)
    next_label = "Berikutnya" if kind == "Pelanggan" else "Selanjutnya"
    assert user.button("Sebelumnya").disabled
    user.click(next_label)
    assert "Halaman 2" in user.text()
    assert user.button(next_label).disabled
    user.click("Sebelumnya")
    assert "Halaman 1" in user.text()
    search = {"Pelanggan": "Cari pelanggan", "Katalog": "Cari Produk/Jasa",
              "Invoice": "Cari invoice", "Penawaran": "Cari penawaran"}[kind]
    control = user.enter(search, "TIDAK_ADA_123")
    emit(control.on_submit, control)
    assert user.button("Sebelumnya").disabled
    assert user.button(next_label).disabled


@pytest.mark.parametrize("kind", DOCUMENTS)
def test_gagal_simpan_database_bisa_dicoba_lagi(user, monkeypatch, kind):
    customer_id = user.customer()
    user.new_document(kind, customer_id)
    user.manual()
    service = DOCUMENTS[kind][0]

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(service, "create_draft", fail)
        user.click("Simpan Draft")
    assert user.rows(DOCUMENTS[kind][2]) == []
    assert "Draft belum tersimpan" in user.text()
    assert user.field("Nama item").value == "Jasa Titip"
    assert not user.button("Simpan Draft").disabled
    user.click("Simpan Draft")
    assert user.document(kind)["grand_total"] == 100000


@pytest.mark.parametrize("label,image_type", IMAGES)
def test_gambar_yang_hilang_tidak_membuat_halaman_crash(
    user, tmp_path, monkeypatch, label, image_type,
):
    user.profile()
    source = tmp_path / "logo_asli.png"
    Image.new("RGB", (16, 16), "blue").save(source)

    async def pick_files(self, **kwargs):
        return [SimpleNamespace(path=str(source))]

    monkeypatch.setattr(ft.FilePicker, "pick_files", pick_files)
    user.click(f"Pilih & Simpan {label}")
    with closing(connect(user.path)) as connection:
        profile = get_business_profile(connection)
    saved_path = (user.path.parent / profile[f"{image_type}_path"]).resolve()
    assert saved_path.is_relative_to(tmp_path.resolve())
    saved_path.unlink()
    page = Page()
    application.main(page)
    reopened = User(page, user.path)
    assert "tidak ditemukan" in reopened.text().lower()
    assert not reopened.button(f"Pilih & Simpan {label}").disabled
