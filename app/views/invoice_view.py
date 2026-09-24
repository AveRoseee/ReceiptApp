from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3

import flet as ft

from app.database import connect
from app.services import customer_service
from app.services import invoice_service as service
from app.services.business_profile_service import get_business_profile
from app.views.invoice_editor import InvoiceEditor


logger = logging.getLogger(__name__)

PAGE_SIZE = 20

STATUS_LABELS = {
    "DRAFT": "Draft",
    "ISSUED": "Diterbitkan",
    "CANCELLED": "Dibatalkan",
}


class SnapshotReadError(ValueError):
    pass


def format_rupiah(value: int) -> str:
    return "Rp" + f"{value:,}".replace(",", ".")


def format_scaled(value: int, digits: int) -> str:
    whole, fraction =divmod(value, 10**digits)
    text = f"{whole:,}".replace(",", ".")

    if fraction:
        text += "," + f"{fraction:0{digits}d}".rstrip("0")

    return text


def read_snapshot(raw: str, label: str) -> dict:
    try:
        snapshot = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise SnapshotReadError(
            f"Snapshot {label} tidak dapat dibaca."
        ) from error

    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != 1
        or not isinstance(snapshot.get("data"), dict)
        or not isinstance(snapshot["data"].get("name"), str)
        or not snapshot["data"]["name"].strip()
    ):
        raise SnapshotReadError(
            f"Format snapshot {label} tidak didukung."
        )

    return snapshot["data"]


def build_invoice_view(
    page: ft.Page,
    database_path: Path
) -> ft.Column:
    offset = 0
    current_search = ""
    current_status = None

    search_input = ft.TextField(
        label = "Cari invoice",
        hint_text = "Nomor atau nama pelanggan",
        col = {"xs": 12, "md": 6}
    )

    status_filter = ft.Dropdown(
        label = "Status",
        value = "ALL",
        col = {"xs": 12, "md": 6},
        options = [
            ft.DropdownOption(
                key = "ALL",
                text = "Semua status",
            ),
            *[
                ft.DropdownOption(key = key, text = label)
                for key, label in STATUS_LABELS.items()
            ],
        ],
    )

    invoice_list = ft.Column(spacing = 12)
    page_info = ft.Text()
    detail_body = ft.Column(spacing = 16)

    def notify(message: str) -> None:
        page.show_dialog(
            ft.SnackBar(
                content = ft.Text(
                    message,
                    color = ft.Colors.WHITE,
                ),
                bgcolor = ft.Colors.RED_700,
            )
        )

    def party_section(
        title: str,
        data: dict,
        business = False,
    ) -> ft.Column:
        labels = {
            "name": "Nama",
            "company_name": "Nama Perusahaan",
            "address": "Alamat Perusahaan",
            "phone": "Telepon",
            "whatsapp": "WhatsApp",
            "email": "Email",
            "npwp": "NPWP",
        }

        if business:
            labels.update(
                {
                    "website": "Website",
                    "responsible_person": "Penanggung Jawab",
                    "bank_name": "Nama Bank",
                    "bank_account_number": "Nomor Rekening",
                    "bank_account_name": "Nama Pemilik Rekening",
                }
            )

        controls = [
            ft.Text(
                title,
                size = 18,
                weight = ft.FontWeight.BOLD,
            )
        ]

        for key, label in labels.items():
            value = data.get(key)

            if value:
                controls.append(
                    ft.Text(
                        f"{label}: {value}",
                        selectable=True,
                    )
                )

        if len(controls) == 1:
            controls.append(
                ft.Text("Data belum diisi.")
            )

        return ft.Column(
            spacing=6,
            controls=controls,
        )

    def item_section(item: dict) -> ft.Container:
        return ft.Container(
            padding = 12,
            bgcolor = ft.Colors.GREY_100,
            border_radius = 8,
            content = ft.Column(
                spacing = 6,
                controls = [
                    ft.Text(
                        f"{item['position']}. {item['name_snapshot']}",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        item["description"],
                        visible = bool(item["description"])
                    ),
                    ft.Text(
                        f"{format_scaled(item['quantity_milli'], 3)} "
                        f"{item['unit']} ×"
                        f"{format_rupiah(item['unit_price'])}"
                    ),
                    ft.Text(
                        f"Total Item: "
                        f"{format_rupiah(item['line_total'])}"
                    ),
                ],
            )
        )

    def handle_detail(event) -> None:
        try:
            with closing(connect(database_path)) as connection:
                invoice = service.get_invoice(
                    connection,
                    event.control.data,
                )

                if invoice["number"] is None:
                    business = get_business_profile(connection) or {}
                    customer = customer_service.get_customer(
                        connection,
                        invoice["customer_id"],
                    )
                else:
                    business = read_snapshot(
                        invoice["business_snapshot"],
                        "usaha",
                    )
                    customer = read_snapshot(
                        invoice["customer_snapshot"],
                        "pelanggan",
                    )

            title = (
                invoice["number"]
                or f"Draft #{invoice['id']}"
            )
            status_label = STATUS_LABELS.get(
                invoice["document_status"],
                invoice["document_status"],
            )

            discount_label = "Diskon"

            if invoice["discount_type"] == "PERCENT":
                rate = format_scaled(
                    invoice["discount_value"],
                    2,
                )
                discount_label += f" ({rate}%)"

            tax_rate = format_scaled(
                invoice["tax_rate_bps"],
                2,
            )

            controls = [
                ft.Text(
                    title,
                    size=23,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    f"Status: {status_label}"
                ),
                ft.Text(
                    f"Tanggal: {invoice['issue_date']}"
                ),
                ft.Text(
                    f"Jatuh tempo: "
                    f"{invoice['due_date'] or 'Tidak ditentukan'}"
                ),
                ft.Divider(),
                party_section(
                    "Usaha",
                    business,
                    business = True,
                ),
                party_section("Pelanggan", customer),
                ft.Divider(),
                ft.Text(
                    "Item Invoice",
                    size = 18,
                    weight = ft.FontWeight.BOLD,
                ),
                *[
                    item_section(item)
                    for item in invoice["items"]
                ],
            ]

            if not invoice["items"]:
                controls.append(
                    ft.Text("Draft ini belum memiliki item.")
                )

            controls.extend(
                [
                    ft.Divider(),
                    ft.Text(
                        f"Subtotal: "
                        f"{format_rupiah(invoice['subtotal'])}"
                    ),
                    ft.Text(
                        f"{discount_label}: "
                        f"{format_rupiah(invoice['discount_amount'])}"
                    ),
                    ft.Text(
                        f"Pajak ({tax_rate}%): "
                        f"{format_rupiah(invoice['tax_amount'])}"
                    ),
                    ft.Text(
                        f"Total: "
                        f"{format_rupiah(invoice['grand_total'])}",
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        "Catatan",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(invoice["notes"] or "-"),
                    ft.Text(
                        "Syarat dan Ketentuan",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(invoice["terms"] or "-")
                ]
            )

        except (
            service.InvoiceValidationError,
            service.InvoiceNotFoundError,
            customer_service.CustomerValidationError,
            customer_service.CustomerNotFoundError,
            SnapshotReadError,
        ) as error:
            notify(str(error))
            return

        except (sqlite3.Error, OSError):
            logger.exception(
                "Gagal membaca detail invoice"
            )
            notify("Detail invoice belum dapat dimuat.")
            return

        detail_body.controls = controls
        list_section.visible = False
        detail_section.visible = True
        page.update()

    def invoice_card(invoice: dict) -> ft.Container:
        title = (
            invoice["number"]
            or f"Draft #{invoice['id']}"
        )
        status_label = STATUS_LABELS.get(
            invoice["document_status"],
            invoice["document_status"],
        )

        return ft.Container(
            padding = 16,
            border_radius = 8,
            bgcolor = ft.Colors.GREY_100,
            content = ft.Column(
                spacing = 8,
                controls = [
                    ft.Text(
                        title,
                        size = 17,
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        invoice["customer_name"]
                        or "Nama tidak tersedia"
                    ),
                    ft.Text(
                        f"{invoice['issue_date']} · "
                        f"{status_label}"
                    ),
                    ft.Text(
                        format_rupiah(invoice["grand_total"])
                    ),
                    ft.TextButton(
                        content="Lihat Detail",
                        data=invoice["id"],
                        on_click=handle_detail,
                    ),
                    ft.TextButton(
                        content="Edit Draft",
                        data=invoice["id"],
                        visible=(
                            invoice["document_status"] == "DRAFT"
                            and invoice["number"] is None
                        ),
                        on_click=handle_open_editor,
                    ),
                ]
            )
        )

    def refresh_list() -> None:
        nonlocal offset

        try:
            with closing(connect(database_path)) as connection:
                invoices = service.list_invoices(
                    connection,
                    search = current_search,
                    status = current_status,
                    limit = PAGE_SIZE + 1,
                    offset = offset,
                )

                if not invoices and offset > 0:
                    offset = 0
                    invoices = service.list_invoices(
                        connection,
                        search=current_search,
                        status=current_status,
                        limit=PAGE_SIZE + 1,
                        offset=0,
                    )

        except (
            service.InvoiceValidationError,
            sqlite3.Error,
            OSError,
        ): 
            logger.exception(
                "Gagal memuat daftar invoice"
            )

            invoice_list.controls = [
                ft.Text(
                    "Daftar invoice belum dapat dimuat. "
                    "Klik Cari untuk mencoba kembali.",
                    color = ft.Colors.RED_700,
                )
            ]
            previous_button.disabled = True
            next_button.disabled = True
            page_info.value = ""
            return
        
        visible = invoices[:PAGE_SIZE]

        invoice_list.controls = [
            invoice_card(row)
            for row in visible
        ]

        if not visible:
            invoice_list.controls = [
                ft.Text(
                    "Belum ada invoice yang sesuai "
                    "dengan pencarian dan filter."
                )
            ]

        previous_button.disabled = offset == 0
        next_button.disabled = len(invoices) <= PAGE_SIZE

        page_info.value = (
            f"Halaman {offset // PAGE_SIZE + 1}"
            f" · {len(visible)} invoice ditampilkan"
        )

    def handle_search(event) -> None:
        nonlocal offset, current_search, current_status

        offset = 0
        current_search = (search_input.value or "").strip()
        current_status = (
            None
            if status_filter.value == "ALL"
            else status_filter.value
        )

        refresh_list()
        page.update()

    def handle_page(event) -> None:
        nonlocal offset

        offset = max(
            0,
            offset + event.control.data,
        )

        refresh_list()
        page.update()

    def handle_back(event) -> None:
        detail_section.visible = False
        list_section.visible = True

        refresh_list()
        page.update()

    def handle_cancel_editor(event):
        editor.control.visible = False
        detail_section.visible = False
        list_section.visible = True

        refresh_list()
        page.update()

    def handle_saved(invoice):
        nonlocal offset, current_search, current_status

        offset = 0
        current_search = ""
        current_status = "DRAFT"
        search_input.value = ""
        status_filter.value = "DRAFT"

        handle_cancel_editor(None)

        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(
                    f"Draft #{invoice['id']} berhasil disimpan."
                ),
            )
        )

    editor = InvoiceEditor(
        page,
        database_path,
        on_saved=handle_saved,
        on_cancel=handle_cancel_editor,
    )

    def handle_open_editor(event):
        try:
            editor.open(event.control.data)

        except (ValueError, LookupError) as error:
            notify(str(error))
            return

        except (sqlite3.Error, OSError):
            logger.exception("Gagal membuka form invoice")
            notify("Form invoice belum dapat dimuat.")
            return

        list_section.visible = False
        detail_section.visible = False
        editor.control.visible = True

        page.update()

    previous_button = ft.TextButton(
        content = "Sebelumnya",
        data = -PAGE_SIZE,
        on_click = handle_page
    )

    next_button = ft.TextButton(
        content = "Selanjutnya",
        data = PAGE_SIZE,
        on_click = handle_page,
    )

    search_input.on_submit = handle_search
    status_filter.on_select = handle_search

    list_section = ft.Column(
        spacing = 16,
        controls = [
            ft.Button(
                content="Tambah Invoice",
                data=None,
                on_click=handle_open_editor,
            ),
            ft.ResponsiveRow(
                controls = [
                    search_input,
                    status_filter,
                ],
            ),
            ft.Button(
                content = "Cari",
                on_click = handle_search,
            ),
            invoice_list,
            page_info,
            ft.Row(
                controls = [
                    previous_button,
                    next_button,
                ],
            ),
        ],
    )

    detail_section = ft.Column(
        visible = False,
        spacing = 16,
        controls = [
            ft.TextButton(
                content = "Kembali ke Daftar",
                on_click = handle_back,
            ),
            detail_body,
        ],
    )

    refresh_list()

    view = ft.Column(
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        spacing=20,
        controls=[
            ft.Text(
                "Invoice",
                size=28,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(
                "Buat invoice langsung tanpa penawaran. Tambahkan barang, "
                "jasa titip, dan pengiriman sebagai item terpisah. "
                "Invoice disimpan sebagai draft dan belum memiliki nomor resmi."
            ),
            list_section,
            detail_section,
            editor.control,
        ],
    )

    view.data = refresh_list
    return view