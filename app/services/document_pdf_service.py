"""Offline document rendering and atomic output; no UI dependencies."""
from contextlib import closing, contextmanager
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

from app.database import connect
from app.repositories.document_repository import database_path
from app.services.invoice_service import get_invoice
from app.services.payment_service import get_invoice_summary
from app.services.receipt_service import get_receipt, read_snapshot
from app.services.invoice_pdf_service import InvoicePdfError, _snapshot, _image, render_invoice, rupiah


class DocumentPdfError(ValueError):
    pass


@contextmanager
def _read(connection):
    own = not connection.in_transaction
    if own:
        connection.execute("BEGIN")
    try:
        yield
    finally:
        if own:
            connection.rollback()


def safe_filename(number):
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", str(number)).strip("-")[:100]
    return (name or "dokumen") + ".pdf"


def _path(connection):
    path = database_path(connection)
    if path is None:
        raise DocumentPdfError("PDF memerlukan database tersimpan di disk.")
    return path.resolve()


def invoice_bytes(connection, invoice_id):
    path = _path(connection)
    with _read(connection):
        invoice = get_invoice(connection, invoice_id)
        if invoice["document_status"] != "ISSUED" or not invoice["number"]:
            raise DocumentPdfError("PDF hanya tersedia untuk invoice terbit.")
        business = _snapshot(invoice["business_snapshot"])
        customer = _snapshot(invoice["customer_snapshot"])
        summary = get_invoice_summary(connection, invoice_id)
    return render_invoice(path, invoice, business, customer, summary)


def receipt_bytes(connection, receipt_id):
    path = _path(connection)
    with _read(connection):
        receipt = get_receipt(connection, receipt_id)
        data = read_snapshot(receipt["document_snapshot"])
    name = "ReceiptVera"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(Path(reportlab.__file__).parent / "fonts/Vera.ttf")))
    glyphs = pdfmetrics.getFont(name).face.charToGlyph
    normal = ParagraphStyle("Receipt", fontName=name, fontSize=11, leading=16, spaceAfter=8)
    title = ParagraphStyle("ReceiptTitle", parent=normal, fontSize=23, leading=29)
    def p(value, style=normal):
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        if any(ord(c) not in glyphs for c in text if c != "\n"):
            raise DocumentPdfError("Ada karakter yang belum didukung font kwitansi.")
        return Paragraph(escape(text).replace("\n", "<br/>"), style)
    business, customer, payment = data["business"], data["customer"], data["payment"]
    story = [p("KWITANSI", title), p(receipt["number"])]
    if receipt["status"] == "VOID":
        story.extend([p("DIBATALKAN", title), p(receipt["void_reason"])])
    logo = _image(path, business.get("logo_path"), 45*mm, 22*mm)
    if logo:
        logo.hAlign = "LEFT"
        story.append(logo)
    story.extend([p(business["name"]), p(business.get("address")),
        p("Kontak: " + (business.get("phone") or business.get("email") or "-")),
        Spacer(1, 6*mm), p("Diterima dari: " + customer["name"]),
        p("Untuk invoice: " + data["invoice"]["number"]),
        p("Tanggal pembayaran: " + payment["payment_date"]),
        p("Tanggal kwitansi: " + receipt["issued_date"]),
        p("Nominal: " + rupiah(payment["amount"])), p(data["amount_words"]),
        p("Metode: " + payment["method"]), p("Referensi: " + (payment["reference"] or "-")),
        p("Catatan: " + (payment["notes"] or "-")), p(receipt["description"]),
    ])
    for field, label in (("signature_path", "Tanda tangan"), ("stamp_path", "Stempel")):
        image = _image(path, business.get(field), 45*mm, 25*mm)
        if image:
            image.hAlign = "LEFT"
            story.append(KeepTogether([p(label), image]))
    story.append(p(business.get("responsible_person")))
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(name, 8)
        canvas.drawString(18*mm, 12*mm, receipt["number"])
        canvas.drawRightString(A4[0]-18*mm, 12*mm, f"Halaman {doc.page}")
        canvas.restoreState()
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                      topMargin=18*mm, bottomMargin=20*mm).build(
        story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def _write(connection, output_path, data):
    destination = Path(output_path).resolve()
    db_path = _path(connection)
    if destination.suffix.lower() != ".pdf" or destination == db_path or destination.is_relative_to(db_path.parent / "assets"):
        raise DocumentPdfError("Pilih lokasi file PDF di luar database dan folder gambar.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return destination
    except OSError as error:
        raise DocumentPdfError("PDF gagal disimpan. Periksa folder tujuan dan izin akses.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def generate_invoice_pdf(connection, invoice_id, output_path):
    return _write(connection, output_path, invoice_bytes(connection, invoice_id))


def generate_receipt_pdf(connection, receipt_id, output_path):
    return _write(connection, output_path, receipt_bytes(connection, receipt_id))


def build_pdf(database_path, kind, document_id):
    with closing(connect(database_path)) as connection:
        return (invoice_bytes if kind == "INVOICE" else receipt_bytes)(connection, document_id)


def open_pdf(database_path, kind, document_id, number):
    directory = Path(database_path).resolve().parent / "exports"
    directory.mkdir(exist_ok=True)
    destination = directory / (uuid4().hex + "-" + safe_filename(number))
    with closing(connect(database_path)) as connection:
        generator = generate_invoice_pdf if kind == "INVOICE" else generate_receipt_pdf
        generator(connection, document_id, destination)
    os.startfile(str(destination))
    return destination
