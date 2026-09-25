from contextlib import closing
from io import BytesIO
import json
from uuid import uuid4

from PIL import Image
from pypdf import PdfReader
import pytest

from app.database import connect, initialize_database
from app.services import (
    customer_service,
    invoice_service,
    payment_service,
)
from app.services import invoice_pdf_service as pdf
from app.services.business_image_service import save_business_image
from app.services.business_profile_service import save_business_profile


@pytest.fixture
def case(tmp_path):
    path = initialize_database(tmp_path / "business.db")

    with closing(connect(path)) as connection:
        save_business_profile(
            connection,
            {
                "name": "Jastip Jepang",
                "address": "Jakarta",
                "bank_name": "Bank Contoh",
                "bank_account_number": "1234567890",
                "bank_account_name": "Pemilik Usaha",
            },
        )

        customer = customer_service.create_customer(
            connection,
            {"name": "Pelanggan Awal"},
        )

    return path, customer["id"]


def create(case, *, items=None, publish=True, **options):
    path, customer_id = case

    if items is None:
        items = [
            {
                "name_snapshot": name,
                "quantity_milli": 1000,
                "unit": "paket",
                "unit_price": price,
            }
            for name, price in [
                ("Barang titipan", 600000),
                ("Jasa titip", 100000),
                ("Pengiriman", 25000),
            ]
        ]

    with closing(connect(path)) as connection:
        invoice = invoice_service.create_draft(
            connection,
            {
                "customer_id": customer_id,
                "issue_date": "2026-09-24",
                "items": items,
                **options,
            },
        )

    if publish:
        invoice = invoice_service.publish_invoice(
            path,
            invoice["id"],
        )

    return invoice


def read(case, invoice):
    data = pdf.build_invoice_pdf(case[0], invoice["id"])

    assert data.startswith(b"%PDF-")

    reader = PdfReader(BytesIO(data))
    text = "\n".join(
        page.extract_text()
        for page in reader.pages
    )

    return reader, text


def pay(case, invoice, amount):
    with closing(connect(case[0])) as connection:
        return payment_service.record_payment(
            connection,
            invoice["id"],
            {
                "payment_date": "2026-09-24",
                "amount": amount,
                "method": "TRANSFER",
            },
            request_id=str(uuid4()),
        )


def test_invoice_dan_total(case):
    invoice = create(case)
    reader, text = read(case, invoice)

    assert len(reader.pages) == 1

    for value in [
        invoice["number"],
        "Jastip Jepang",
        "Pelanggan Awal",
        "Barang titipan",
        "Jasa titip",
        "Pengiriman",
        "Rp725.000",
        "Belum dibayar",
        "1234567890",
        "Halaman 1",
    ]:
        assert value in text


def test_snapshot_tidak_mengikuti_perubahan_master(case):
    invoice = create(case)

    with closing(connect(case[0])) as connection:
        save_business_profile(
            connection,
            {"name": "Usaha Baru"},
        )

        customer_service.update_customer(
            connection,
            case[1],
            {"name": "Pelanggan Baru"},
        )

    _, text = read(case, invoice)

    assert "Jastip Jepang" in text
    assert "Pelanggan Awal" in text
    assert "Usaha Baru" not in text
    assert "Pelanggan Baru" not in text


def test_dp_pelunasan_dan_pembatalan(case):
    invoice = create(case)

    pay(case, invoice, 300000)
    assert "Rp425.000" in read(case, invoice)[1]

    final = pay(case, invoice, 425000)
    assert "Status: Lunas" in read(case, invoice)[1]

    with closing(connect(case[0])) as connection:
        payment_service.void_payment(
            connection,
            final["id"],
            "Salah input",
        )

    text = read(case, invoice)[1]

    assert "Rp425.000" in text
    assert "Status: Dibayar sebagian" in text


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED"])
def test_status_belum_dapat_dicetak(case, status):
    invoice = create(case, publish=False)

    if status == "CANCELLED":
        with closing(connect(case[0])) as connection:
            connection.execute(
                """
                UPDATE invoices
                SET document_status = ?
                WHERE id = ?
                """,
                (status, invoice["id"]),
            )

    with pytest.raises(pdf.InvoicePdfError):
        pdf.build_invoice_pdf(case[0], invoice["id"])


def test_invoice_tidak_ada(case):
    with pytest.raises(invoice_service.InvoiceNotFoundError):
        pdf.build_invoice_pdf(case[0], 99999)


def test_database_tidak_dibuat_otomatis(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(pdf.InvoicePdfError):
        pdf.build_invoice_pdf(path, 1)

    assert not path.exists()


def test_ekspor_tidak_mengubah_database(case):
    invoice = create(case)

    with closing(connect(case[0])) as connection:
        before = list(connection.iterdump())

    read(case, invoice)
    read(case, invoice)

    with closing(connect(case[0])) as connection:
        assert list(connection.iterdump()) == before


def test_karakter_khusus_dan_kuantitas_pecahan(case):
    invoice = create(
        case,
        items=[
            {
                "name_snapshot": "Figure <Limited> & Stand",
                "description": (
                    "Ukuran < 20 cm\n"
                    "Warna merah & putih"
                ),
                "quantity_milli": 1500,
                "unit": "pcs",
                "unit_price": 100000,
            },
        ],
        notes="Catatan <b>tetap teks</b>",
    )

    _, text = read(case, invoice)

    for value in [
        "Figure <Limited> & Stand",
        "1,5 pcs",
        "Rp150.000",
        "Catatan <b>tetap teks</b>",
    ]:
        assert value in text


def test_banyak_halaman_dengan_header_berulang(case):
    invoice = create(
        case,
        items=[
            {
                "name_snapshot": f"Barang nomor {i:03}",
                "quantity_milli": 1000,
                "unit": "pcs",
                "unit_price": 1000,
            }
            for i in range(100)
        ],
    )

    reader, text = read(case, invoice)

    assert len(reader.pages) > 1
    assert "Barang nomor 099" in text

    for i, page in enumerate(reader.pages, 1):
        page_text = page.extract_text()

        assert f"Halaman {i}" in page_text

        if "Barang nomor" in page_text:
            assert "Barang / Jasa" in page_text


def test_deskripsi_sangat_panjang(case):
    invoice = create(
        case,
        items=[
            {
                "name_snapshot": "Barang panjang",
                "description": (
                    "Keterangan barang. " * 1000
                    + "AKHIR DESKRIPSI"
                ),
                "quantity_milli": 1000,
                "unit": "pcs",
                "unit_price": 500,
            },
        ],
    )

    reader, text = read(case, invoice)

    assert len(reader.pages) > 1
    assert "AKHIR DESKRIPSI" in " ".join(text.split())
    assert "TOTAL INVOICE" in text


def test_nominal_besar_tetap_utuh(case):
    invoice = create(
        case,
        items=[
            {
                "name_snapshot": "Nilai maksimum",
                "quantity_milli": 1000,
                "unit": "pcs",
                "unit_price": 2**63 - 1,
            },
        ],
    )

    _, text = read(case, invoice)

    assert "Rp9.223.372.036.854.775.807" in text


def test_gambar_menggunakan_salinan_saat_terbit(case):
    path = case[0]
    source = path.parent / "source.png"

    Image.new("RGB", (80, 40), "blue").save(source)

    for kind in ("logo", "qris", "signature", "stamp"):
        save_business_image(path, source, kind)

    invoice = create(case)

    Image.new("RGB", (80, 40), "red").save(source)

    for kind in ("logo", "qris", "signature", "stamp"):
        save_business_image(path, source, kind)

    reader, _ = read(case, invoice)

    images = [
        image.image.convert("RGB")
        for page in reader.pages
        for image in page.images
    ]

    assert images

    assert all(
        image.getpixel((0, 0)) == (0, 0, 255)
        for image in images
    )


def test_gambar_snapshot_hilang_dilaporkan(case):
    source = case[0].parent / "source.png"

    Image.new("RGB", (20, 20), "blue").save(source)
    save_business_image(case[0], source, "logo")

    invoice = create(case)
    snapshot = json.loads(invoice["business_snapshot"])["data"]

    (case[0].parent / snapshot["logo_path"]).unlink()

    with pytest.raises(pdf.InvoicePdfError):
        read(case, invoice)


@pytest.mark.parametrize(
    "raw",
    [
        "bukan json",
        "[]",
        '{"schema_version":2,"data":{"name":"Usaha"}}',
    ],
)
def test_snapshot_rusak_ditolak(case, monkeypatch, raw):
    invoice = create(case)
    changed = dict(invoice, business_snapshot=raw)

    monkeypatch.setattr(
        pdf,
        "get_invoice",
        lambda *args: changed,
    )

    with pytest.raises(pdf.InvoicePdfError):
        read(case, invoice)


def test_gambar_di_luar_folder_snapshot_ditolak(case, monkeypatch):
    invoice = create(case)
    snapshot = json.loads(invoice["business_snapshot"])

    snapshot["data"]["logo_path"] = "../outside.png"

    changed = dict(
        invoice,
        business_snapshot=json.dumps(snapshot),
    )

    monkeypatch.setattr(
        pdf,
        "get_invoice",
        lambda *args: changed,
    )

    with pytest.raises(pdf.InvoicePdfError):
        read(case, invoice)


def test_diskon_dan_pajak(case):
    invoice = create(
        case,
        items=[
            {
                "name_snapshot": "Barang A",
                "quantity_milli": 1500,
                "unit": "pcs",
                "unit_price": 100000,
            },
            {
                "name_snapshot": "Barang B",
                "quantity_milli": 2000,
                "unit": "pcs",
                "unit_price": 25000,
            },
        ],
        discount_type="PERCENT",
        discount_value=1000,
        tax_rate_bps=1100,
    )

    _, text = read(case, invoice)

    for value in [
        "Rp200.000",
        "Rp20.000",
        "Rp19.800",
        "Rp199.800",
    ]:
        assert value in text


def test_font_tidak_ditemukan(case):
    invoice = create(case)

    with pytest.raises(pdf.InvoicePdfError):
        pdf.build_invoice_pdf(
            case[0],
            invoice["id"],
            font_path=case[0].parent / "missing.ttf",
        )


def test_karakter_tanpa_glyph_tidak_hilang_diam_diam(case):
    invoice = create(case, notes="東京")

    with pytest.raises(pdf.InvoicePdfError):
        read(case, invoice)