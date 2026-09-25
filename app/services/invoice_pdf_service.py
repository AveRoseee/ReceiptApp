from contextlib import closing
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from app.database import connect
from app.services.invoice_service import get_invoice
from app.services.payment_service import get_invoice_summary


class InvoicePdfError(ValueError):
    pass


def rupiah(value):
    return "Rp" + f"{value:,}".replace(",", ".")


def quantity(value):
    whole, fraction = divmod(value, 1000)

    return str(whole) + (
        "," + f"{fraction:03d}".rstrip("0")
        if fraction
        else ""
    )


def _snapshot(raw):
    try:
        value = json.loads(raw)
        data = value["data"]

        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or not isinstance(data, dict)
            or not isinstance(data.get("name"), str)
            or not data["name"].strip()
            or any(
                v is not None and not isinstance(v, (str, int))
                for v in data.values()
            )
        ):
            raise ValueError

        return data

    except (ValueError, TypeError, KeyError) as error:
        raise InvoicePdfError(
            "Salinan data invoice tidak valid."
        ) from error


def _image(database_path, relative_path, width, height):
    if not relative_path:
        return None

    if not isinstance(relative_path, str):
        raise InvoicePdfError("Lokasi gambar tidak valid.")

    base = database_path.parent.resolve()
    allowed = base / "assets" / "documents"

    relative = Path(relative_path)
    path = (base / relative).resolve()

    if (
        relative.is_absolute()
        or not path.is_relative_to(allowed)
        or not path.is_file()
    ):
        raise InvoicePdfError(
            "Salinan gambar invoice tidak ditemukan."
        )

    try:
        with path.open("rb") as stream:
            data = stream.read(5 * 1024 * 1024 + 1)

            if len(data) > 5 * 1024 * 1024:
                raise ValueError

            with PILImage.open(BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"}:
                    raise ValueError

                w, h = image.size

                if w * h > 16_000_000:
                    raise ValueError

                image.load()

            scale = min(width / w, height / h)

            return Image(
                BytesIO(data),
                width = w * scale,
                height = h * scale,
            )

    except (
        OSError,
        ValueError,
        PILImage.DecompressionBombError,
    ) as error:
        raise InvoicePdfError(
            "Salinan gambar invoice rusak."
        ) from error


def build_invoice_pdf(database_path, invoice_id, *, font_path = None):
    database_path = Path(database_path).resolve()

    if not database_path.is_file():
        raise InvoicePdfError("Database tidak ditemukan.")

    with closing(connect(database_path)) as connection:
        connection.execute("BEGIN")

        invoice = get_invoice(connection, invoice_id)

        if (
            invoice["document_status"] != "ISSUED"
            or not invoice["number"]
        ):
            raise InvoicePdfError(
                "Terbitkan invoice sebelum membuat PDF."
            )

        business = _snapshot(invoice["business_snapshot"])
        customer = _snapshot(invoice["customer_snapshot"])
        summary = get_invoice_summary(connection, invoice_id)

    font_path = (
        Path(font_path)
        if font_path
        else Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    )

    try:
        font_data = font_path.read_bytes()
        font_name = "Invoice-" + sha256(font_data).hexdigest()[:16]

        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(font_name, BytesIO(font_data))
            )

    except Exception as error:
        raise InvoicePdfError(
            "Font PDF tidak dapat dibaca."
        ) from error

    glyphs = pdfmetrics.getFont(font_name).face.charToGlyph

    normal = ParagraphStyle(
        "Invoice Body",
        fontName = font_name,
        fontSize = 9,
        leading = 13,
        spaceAfter = 4,
        splitLongWords = True,
    )

    heading = ParagraphStyle(
        "InvoiceHeading",
        parent = normal,
        fontSize = 18,
        leading = 23,
        spaceAfter = 10,
    )

    right = ParagraphStyle(
        "Invoice Right",
        parent = normal,
        alignment = TA_RIGHT,
    )

    def p(value, style = normal):
        text = (
            str(value or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\t", "   ")
        )

        if any(
            ord(character) not in glyphs
            for character in text
            if text != "\n"
        ):
            raise InvoicePdfError(
                "Ada karakter yang belum didukung font PDF. "
                "Gunakan font TTF yang mendukung karakter tersebut."
            )

        return Paragraph(
            escape(text).replace("\n", "<br/>"),
            style,
        )

    def money(value):
        text = rupiah(value)
        measured = pdfmetrics.stringWidth(text, font_name, 9)

        size = min(9, 9 * (44 * mm - 15) / measured)

        style = ParagraphStyle(
            "InvoiceMoney",
            parent = right,
            fontSize = size,
            leading = 13,
        )

        return p(text, style)

    def lines(data, fields):
        return [
            p(data[key])
            for key in fields
            if data.get(key)
        ]

    def table(rows, widths, *, header = False):
        result = LongTable(
            rows,
            colWidths = widths,
            repeatRows = 1 if header else 0,
            hAlign = "LEFT",
            splitByRow = 1,
            splitInRow = 1,
        )

        commands = {
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        }

        if header:
            commands += [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#eaf0f8"),
                ),
                (
                    "LINEBELOW",
                    (0, 0),
                    (-1, -1),
                    0.3,
                    colors.HexColor("#d7dee8")
                ),
            ]

        result.setStyle(TableStyle(commands))
        return result

    story = [
        p("INVOICE", heading),
        p(invoice["number"])
    ]

    logo = _image(
        database_path,
        business.get("logo_path"),
        42 * mm,
        22 * mm,
    )

    if logo:
        logo.hAlign = "LEFT"
        story.extend([logo, Spacer(1, 4 * mm)])

    story.extend(
        lines(
            business,
            (
                "name",
                "address",
                "phone",
                "whatsapp",
                "email",
                "website",
            ),
        )
    )

    if business.get("npwp"):
        story.append(p("NPWP: " + business["npwp"]))

    story.extend([
        Spacer(1, 4 * mm),
        p("Ditagihkan kepada"),
        *lines(
            customer,
            ("name", "company_name", "address", "phone", "email"),
        ),
        p("Tanggal invoice: " + invoice["issue_date"]),
        p("Jatuh tempo: " + (invoice["due_date"] or "-")),
        Spacer(1, 4 * mm),
    ])

    rows = [[
        p(title)
        for title in ("Barang / Jasa", "Jumlah", "Harga", "Total")
    ]]

    for item in invoice["items"]:
        description = item["name_snapshot"]

        if item["description"]:
            description += "\n" + item["description"]

        rows.append([
            p(description),
            p(
                quantity(item["quantity_milli"])
                + " "
                + item["unit"]
            ),
            money(item["unit_price"]),
            money(item["line_total"]),
        ])

    story.extend([
        table(
            rows,
            [64 * mm, 22 * mm, 44 * mm, 44 * mm],
            header = True,
        ),
        Spacer(1, 4 * mm)
    ])

    totals = [
        ("Subtotal", invoice["subtotal"]),
        ("Diskon", invoice["discount_amount"]),
        ("Pajak", invoice["tax_amount"]),
        ("TOTAL INVOICE", invoice["grand_total"]),
        ("Sudah dibayar", summary["paid_amount"]),
        ("SISA TAGIHAN", summary["balance_due"]),
    ]

    settlement_labels = {
        "UNPAID": "Belum Dibayar",
        "PARTIAL": "Dibayar sebagian",
        "PAID": "Lunas",
    }

    story.append(
        KeepTogether([
            table(
                [
                    [p(label), p(rupiah(value), right)]
                    for label, value in totals
                ],
                [100 * mm, 74 * mm],
            ),
            p(
                "Status: "
                + settlement_labels[summary["settle_status"]]
            ),
            p(
                "Saldo pembayar per "
                + datetime.now().strftime("%Y-%m-%d %H:%M")
            ),
        ])
    )

    for label, key in (
        ("Catatan", "notes"),
        ("Syarat", "terms"),
    ):
        if invoice[key]:
            story.extend([
                Spacer(1, 4 * mm),
                p(label),
                p(invoice[key]),
            ])

    bank = lines(
        business,
        (
            "bank_name",
            "bank_account_number",
            "bank_account_name",
        ),
    )

    if bank:
        story.extend([
            Spacer(1, 4 * mm),
            p("Informasi transfer"),
            *bank,
        ])

    image_cells = []

    for key, label, width, height in (
        ("qris_path", "QRIS", 42 * mm, 42 * mm),
        ("signature_path", "Tanda Tangan", 45 * mm, 22 * mm),
        ("stamp_path", "Stempel", 30 * mm, 25 * mm),
    ):
        image = _image(
            database_path,
            business.get(key),
            width,
            height,
        )

        if image:
            image.hAlign = "LEFT"
            image.cells.append([p(label), image])

    if image_cells:
        story.append(
            KeepTogether([
                Spacer(1, 4 * mm),
                table(
                    [image_cells],
                    [174 * mm / len(image_cells)] * len(image_cells),
                ),
                *lines(business, ("responsible_person",)),
            ])
        )

    elif business.get("responsible_person"):
        story.append(p(business["responsible_person"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font_name, 0)
        canvas.setFillColor(colors.HexColor("#566274"))

        canvas.drawString(
            18 * mm,
            12 * mm,
            "Invoice - " + invoice["number"],
        )

        canvas.drawRightString(
            A4[0] - 18 * mm,
            12 * mm,
            f"Halaman {document.page}"
        )

        canvas.restoreState()

    output = BytesIO()

    document = SimpleDocTemplate(
        output,
        pagesize = A4,
        leftMargin = 18 * mm,
        rightMargin = 18 * mm,
        topMargin = 16 * mm,
        bottomMargin = 20 * mm,
        title = "Invoice " + invoice["number"],
        author = business["name"],
    )

    document.build(
        story,
        onFirstPage = footer,
        onLaterPages= footer,
    )

    return output.getvalue()