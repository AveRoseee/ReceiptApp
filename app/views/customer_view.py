from contextlib import closing
from pathlib import Path
import logging
import sqlite3

import flet as ft

from app.database import connect
from app.services import customer_service as service


logger = logging.getLogger(__name__)

PAGE_SIZE = 20

FIELD_LABELS = {
    "name": "Nama pelanggan / kontak *",
    "company_name": "Nama perusahaan",
    "address": "Alamat",
    "phone": "Nomor telepon",
    "whatsapp": "Nomor WhatsApp",
    "email": "Email",
    "npwp": "NPWP",
    "notes": "Catatan",
}


def build_customer_view(
    page: ft.Page,
    database_path: Path,
) -> ft.Column:
    editing_id: int | None = None
    offset = 0
    current_search = ""
    include_archived = False

    fields = {
        name: ft.TextField(
            label=label,
            multiline=name in {"address", "notes"},
            min_lines=2 if name in {"address", "notes"} else 1,
            col=(
                12
                if name in {"address", "notes"}
                else {"xs": 12, "md": 6}
            ),
        )
        for name, label in FIELD_LABELS.items()
    }

    customer_type = ft.Dropdown(
        label="Jenis pelanggan",
        value="PERSONAL",
        col=12,
        options=[
            ft.DropdownOption(
                key="PERSONAL",
                text="Perorangan",
            ),
            ft.DropdownOption(
                key="COMPANY",
                text="Perusahaan",
            ),
        ],
    )

    form_title = ft.Text(
        "Tambah Pelanggan",
        size=20,
        weight=ft.FontWeight.BOLD,
    )

    customer_list = ft.Column(spacing=12)
    page_info = ft.Text()

    search_input = ft.TextField(
        label="Cari pelanggan",
        hint_text="Nama, perusahaan, telepon, atau WhatsApp",
        expand=True,
    )

    archive_checkbox = ft.Checkbox(
        label="Sertakan pelanggan diarsipkan",
        value=False,
    )

    def notify(message: str, is_error: bool = False) -> None:
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(
                    message,
                    color=ft.Colors.WHITE,
                ),
                bgcolor=(
                    ft.Colors.RED_700
                    if is_error
                    else ft.Colors.GREEN_700
                ),
            )
        )

    def open_editor(customer: dict | None = None) -> None:
        nonlocal editing_id

        editing_id = customer["id"] if customer else None

        form_title.value = (
            "Edit Pelanggan"
            if customer
            else "Tambah Pelanggan"
        )

        customer_type.value = (
            customer["type"] if customer else "PERSONAL"
        )

        for name, control in fields.items():
            control.value = (
                customer.get(name) or ""
                if customer
                else ""
            )
            control.error = None

        editor.visible = True
        list_section.visible = False
        page.update()

    def close_editor(event=None) -> None:
        editor.visible = False
        list_section.visible = True
        page.update()

    def handle_new(event) -> None:
        open_editor()

    def handle_edit(event) -> None:
        try:
            with closing(connect(database_path)) as connection:
                customer = service.get_customer(
                    connection,
                    event.control.data,
                )

            open_editor(customer)

        except (
            service.CustomerValidationError,
            service.CustomerNotFoundError,
        ) as error:
            notify(str(error), is_error=True)

        except (sqlite3.Error, OSError):
            logger.exception("Gagal membaca pelanggan")
            notify(
                "Data pelanggan belum dapat dimuat.",
                is_error=True,
            )

    def handle_status(event) -> None:
        customer_id, active = event.control.data

        try:
            with closing(connect(database_path)) as connection:
                service.set_customer_active(
                    connection,
                    customer_id,
                    active,
                )

        except (
            service.CustomerValidationError,
            service.CustomerNotFoundError,
        ) as error:
            notify(str(error), is_error=True)
            return

        except (sqlite3.Error, OSError):
            logger.exception("Gagal mengubah status pelanggan")
            notify(
                "Status pelanggan belum berhasil diubah.",
                is_error=True,
            )
            return

        refresh_list()
        page.update()

        notify(
            "Pelanggan diaktifkan kembali."
            if active
            else "Pelanggan diarsipkan."
        )

    def customer_card(customer: dict) -> ft.Container:
        active = bool(customer["is_active"])

        details = [
            customer["company_name"],
            customer["phone"] or customer["whatsapp"],
            "Aktif" if active else "Diarsipkan",
        ]

        return ft.Container(
            padding=16,
            border_radius=8,
            bgcolor=ft.Colors.GREY_100,
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Text(
                        customer["name"],
                        size=17,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        " · ".join(
                            value for value in details if value
                        )
                    ),
                    ft.Row(
                        wrap=True,
                        controls=[
                            ft.TextButton(
                                content="Edit",
                                data=customer["id"],
                                on_click=handle_edit,
                            ),
                            ft.TextButton(
                                content=(
                                    "Arsipkan"
                                    if active
                                    else "Aktifkan Kembali"
                                ),
                                data=(
                                    customer["id"],
                                    not active,
                                ),
                                on_click=handle_status,
                            ),
                        ],
                    ),
                ],
            ),
        )

    def refresh_list() -> None:
        nonlocal offset

        try:
            with closing(connect(database_path)) as connection:
                customers = service.list_customers(
                    connection,
                    search=current_search,
                    include_archived=include_archived,
                    limit=PAGE_SIZE + 1,
                    offset=offset,
                )

                # Kembali ke awal jika halaman terakhir menjadi kosong.
                if not customers and offset > 0:
                    offset = 0

                    customers = service.list_customers(
                        connection,
                        search=current_search,
                        include_archived=include_archived,
                        limit=PAGE_SIZE + 1,
                        offset=offset,
                    )

        except (
            sqlite3.Error,
            OSError,
            service.CustomerValidationError,
        ):
            logger.exception("Gagal memuat daftar pelanggan")

            customer_list.controls = [
                ft.Text(
                    "Daftar pelanggan belum dapat dimuat. "
                    "Klik Cari untuk mencoba kembali.",
                    color=ft.Colors.RED_700,
                )
            ]
            previous_button.disabled = True
            next_button.disabled = True
            page_info.value = ""
            return

        visible_customers = customers[:PAGE_SIZE]

        customer_list.controls = [
            customer_card(customer)
            for customer in visible_customers
        ]

        if not visible_customers:
            customer_list.controls = [
                ft.Text(
                    "Tidak ada pelanggan yang cocok. "
                    "Tambahkan pelanggan atau ubah pencarian."
                )
            ]

        previous_button.disabled = offset == 0
        next_button.disabled = len(customers) <= PAGE_SIZE

        page_info.value = (
            f"Halaman {offset // PAGE_SIZE + 1}"
            f" · {len(visible_customers)} pelanggan ditampilkan"
        )

    def handle_search(event) -> None:
        nonlocal offset, current_search, include_archived

        offset = 0
        current_search = (search_input.value or "").strip()
        include_archived = bool(archive_checkbox.value)

        refresh_list()
        page.update()

    def handle_page(event) -> None:
        nonlocal offset

        offset = max(0, offset + event.control.data)

        refresh_list()
        page.update()

    def handle_save(event) -> None:
        if save_button.disabled:
            return

        fields["name"].error = None
        save_button.disabled = True
        page.update()

        try:
            data = {
                name: control.value or ""
                for name, control in fields.items()
            }
            data["type"] = customer_type.value or "PERSONAL"

            with closing(connect(database_path)) as connection:
                if editing_id is None:
                    service.create_customer(connection, data)
                else:
                    service.update_customer(
                        connection,
                        editing_id,
                        data,
                    )

        except service.CustomerValidationError as error:
            if not (fields["name"].value or "").strip():
                fields["name"].error = str(error)

            notify(str(error), is_error=True)

        except service.CustomerNotFoundError as error:
            notify(str(error), is_error=True)

        except (sqlite3.Error, OSError):
            logger.exception("Gagal menyimpan pelanggan")
            notify(
                "Pelanggan belum berhasil disimpan.",
                is_error=True,
            )

        else:
            editor.visible = False
            list_section.visible = True
            refresh_list()
            notify("Pelanggan berhasil disimpan.")

        finally:
            save_button.disabled = False
            page.update()

    save_button = ft.Button(
        content="Simpan Pelanggan",
        on_click=handle_save,
    )

    previous_button = ft.TextButton(
        content="Sebelumnya",
        data=-PAGE_SIZE,
        on_click=handle_page,
    )

    next_button = ft.TextButton(
        content="Berikutnya",
        data=PAGE_SIZE,
        on_click=handle_page,
    )

    search_input.on_submit = handle_search
    archive_checkbox.on_change = handle_search

    editor = ft.Column(
        visible=False,
        spacing=16,
        controls=[
            form_title,
            ft.ResponsiveRow(
                spacing=16,
                run_spacing=16,
                controls=[
                    customer_type,
                    *fields.values(),
                ],
            ),
            ft.Row(
                wrap=True,
                controls=[
                    save_button,
                    ft.TextButton(
                        content="Batal",
                        on_click=close_editor,
                    ),
                ],
            ),
        ],
    )

    list_section = ft.Column(
        spacing=16,
        controls=[
            ft.Button(
                content="Tambah Pelanggan",
                on_click=handle_new,
            ),
            ft.Row(
                controls=[
                    search_input,
                    ft.Button(
                        content="Cari",
                        on_click=handle_search,
                    ),
                ],
            ),
            archive_checkbox,
            customer_list,
            page_info,
            ft.Row(
                controls=[
                    previous_button,
                    next_button,
                ],
            ),
        ],
    )

    refresh_list()

    return ft.Column(
        expand = True,
        scroll = ft.ScrollMode.AUTO,
        spacing = 20,
        controls = [
            ft.Text(
                "Pelanggan",
                size = 28,
                weight = ft.FontWeight.BOLD,
            ),
            editor,
            list_section,
        ]
    )