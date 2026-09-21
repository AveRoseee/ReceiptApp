from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3

import flet as ft

from app.database import connect
from app.services import customer_service
from app.services import quotation_service as service
from app.services.business_profile_service import get_business_profile


logger = logging.getLogger(__name__)

PAGE_SIZE = 20

STATUS_LABELS = {
    "DRAFT": "Draft",
    "SENT": "Diterbitkan",
    "ACCEPTED": "Diterima",
    "REJECTED": "Ditolak",
    "EXPIRED": "Kadaluwarsa",
    "CONVERTED": "Dikoneversi ke invoice",
}


class SnapshotReadError(ValueError):
    pass


def format_rupiah(value: int) -> str:
    return "Rp" + f"{value:,}".replace(",", ".")


def format_scaled(value: int, digits: int) -> str:
    whole, fraction =divmod(value, 10**digits)
    text = f"{whole:,}".replace(",", ".")

    if fraction:
        text += "," + f"{fraction:0{digits}d}".strip("0")

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


def build_quotation_view(
    page: ft.Page,
    database_path: Path
) -> ft.Column:
    offset = 0
    current_search = ""
    current_status = None

    search_input = ft.TextField(
        label = "Cari penawaran",
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

    quotation_list = ft.Column(spacing = 12)
    page_info = ft.Text()
    detail_body = ft.Column(spacing = 16)

    def notify(message: str) -> None:
        page.show_dialog(
            ft.SnackBar(
                content = ft.Text(
                    message,
                    colors = ft.Colors.WHITE,
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
                    ft.Text("Profil usaha belum diisi")
                )

            return ft.Column(
                spacing = 6,
                controls = controls,
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
                quotation = service.get_quotation(
                    connection,
                    event.control.data,
                )

                if quotation["status"] == "DRAFT":
                    business = get_business_profile(connection) or {}
                    customer = customer_service.get_customer(
                        connection,
                        quotation["customer_id"],
                    )
                else:
                    business = read_snapshot(
                        quotation["business_snapshot"],
                        "usaha",
                    )
                    customer = read_snapshot(
                        quotation["customer_snapshot"],
                        "pelanggan",
                    )

            title = (
                quotation["number"]
                or f"Draft #{quotation['id']}"
            )
            status_label = STATUS_LABELS.get(
                quotation["status"],
                quotation["status"],
            )

            discount_label = "Diskon"

            if quotation["discount_type"] == "PERCENT":
                rate = format_scaled(
                    quotation["discount_value"],
                    2,
                )
                discount_label += f" ({rate}%)"

            tax_rate = format_scaled(
                quotation["tax_rate_bps"],
                2,
            )

            controls = [
                ft.Text(
                    title,
                    size = 23,
                    weight = ft.FontWeight.BOLD,
                ),
                ft.Text(
                    f"Tanggal: {quotation['issue_date']}"
                ),
                ft.Text(
                    f"Berlaku sampai: "
                    f"{quotation['valid_until'] or 'Tidak ditemukan'}"
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
                    "Item Penawaran",
                    size = 18,
                    weight = ft.FontWeight.BOLD,
                ),
                *[
                    item_section(item)
                    for item in quotation["items"]
                ],
            ]

            if not quotation["items"]:
                controls.append(
                    ft.Text("Draft ini belum memiliki item.")
                )

            controls.extend(
                [
                    ft.Divider(),
                    ft.Text(
                        f"Subtotal: "
                        f"{format_rupiah(quotation['subtotal'])}"
                    ),
                    ft.Text(
                        f"{discount_label}: "
                        f"{format_rupiah(quotation['discount_amount'])}"
                    ),
                    ft.Text(
                        f"Pajak ({tax_rate}%)"
                        f"{format_rupiah(quotation['tax_rate_bps'])}"
                    ),
                    ft.Text(
                        "Catatan",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(quotation["notes"] or "-"),
                    ft.Text(
                        "Syarat dan Ketentuan",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(quotation["terms"] or "-")
                ]
            )

        except (sqlite3.Error, OSError):
            logger.exception(
                "Gagal membaca detail penawaran"
            )
            notify("Detail penawaran belum dapat dimuat.")
            return

        detail_body.controls = controls
        list_section.visible = False
        detail_section.visible = True
        page.update()

    def quotation_card(quotation: dict) -> ft.Container:
        title = (
            quotation["number"]
            or f"Draft #{quotation['id']}"
        )
        status_label = STATUS_LABELS.get(
            quotation["status"],
            quotation["status"],
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
                        quotation["customer_name"]
                        or "Nama tidak tersedia"
                    ),
                    ft.Text(
                        f"{quotation['issue_date']} · "
                        f"{status_label}"
                    ),
                    ft.Text(
                        format_rupiah(quotation["grand_total"])
                    ),
                    ft.TextButton(
                        content = "Lihat Detail",
                        data = quotation["id"],
                        on_click = handle_detail,
                    )
                ]
            )
        )

    def refresh_list() -> None:
        nonlocal offset

        try:
            with closing(connect(database_path)) as connection:
                quotations = service.list_quotations(
                    connection,
                    search = current_search,
                    status = current_status,
                    limit = PAGE_SIZE + 1,
                    offset = offset,
                )

        except (
            service.QuotationValidationError,
            sqlite3.Error,
            OSError,
        ): 
            logger.exception(
                "Gagal memuat daftar penawaran"
            )

            quotation_list.controls = [
                ft.Text(
                    "Daftar penawaran belum dapat dimuat. "
                    "Klik Cari untuk mencoba kembali.",
                    color = ft.Colors.RED_700,
                )
            ]
            previous_button.disabled = True
            next_button.disabled = True
            page_info.value = ""
            return
        
        visible = quotations[:PAGE_SIZE]

        quotation_list.controls = [
            quotation_card(row)
            for row in visible
        ]

        if not visible:
            quotation_list.controls = [
                ft.Text(
                    "Belum ada penawaran yang sesuai "
                    "dengan pencarian dan filter."
                )
            ]

        previous_button.disabled = offset == 0
        next_button.disabled = len(quotations) <= PAGE_SIZE

        page_info.value = (
            f"Halaman {offset // PAGE_SIZE + 1}"
            f" · {len(visible)} penawaran ditampilkan"
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
            quotation_list,
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

    return ft.Column(
        expand = True,
        scroll = ft.ScrollMode.AUTO,
        spacing = 20,
        controls = [
            ft.Text(
                "Penawaran",
                size = 28,
                weight = ft.FontWeight.BOLD,
            ),
            list_section,
            detail_section,
        ],
    )