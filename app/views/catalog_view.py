from contextlib import closing
import logging
from pathlib import Path
import re
import sqlite3

import flet as ft

from app.database import connect
from app.services import catalog_service as service


logger = logging.getLogger(__name__)

PAGE_SIZE = 20


def build_catalog_view(
    page: ft.Page,
    database_path: Path,
) -> ft.Column:
    editing_id: int | None = None
    offset = 0
    current_search = ""
    include_archived = False


    fields = {
        "name": ft.TextField(
            label = "Nama Produk/Jasa",
            col = 12,
        ),
        "sku": ft.TextField(
            label = "SKU (Opsional)",
            col = {"xs": 12, "md": 6}
        ),
        "unit": ft.TextField(
            label = "Satuan *",
            hint_text = "Contoh: pcs, jam, project",
            col = {"xs": 12, "md": 6},
        ),
        "default_price": ft.TextField(
            label = "Harga default (Rp) *",
            hint_text = "Contoh: 150000, tanpa titik atau koma",
            col = 12
        ),
        "description": ft.TextField(
            label = "Deskripsi",
            multiline = True,
            min_lines = 2,
            col = 12,
        ),
    }

    item_type = ft.Dropdown(
        label = "Jenis Item",
        value = "PRODUCT",
        col = 12,
        options = [
            ft.DropdownOption(key = "PRODUCT", text = "Product"),
            ft.DropdownOption(key = "SERVICE", text = "Jasa"),
        ],
    )

    form_title = ft.Text(
        "Tambah Item",
        size = 20,
        weight = ft.FontWeight.BOLD,
    )

    item_list = ft.Column(spacing = 12)
    page_info = ft.Text()

    search_input = ft.TextField(
        label = "Cari Produk/Jasa",
        hint_text =  "Nama Produk, SKU, atau Deskripsi"
    )

    archive_checkbox = ft.Checkbox(
        label = "Sertakan Produk/Jasa yang diarsipkan",
        value = False,
    )

    def notify(message: str, is_error: bool = False) -> None:
        page.show_dialog(
            ft.SnackBar(
                content = ft.Text(
                    message,
                    color = ft.Colors.WHITE,
                ),
                bgcolor = (
                    ft.Colors.RED_700
                    if is_error
                    else ft.Colors.GREEN_700
                ),
            )
        )

    def open_editor(item: dict | None = None) -> None:
        nonlocal editing_id

        editing_id = item["id"] if item else None

        form_title.value = (
            "Edit Produk" if item is not None else "Tambah Item"
        )

        defaults = {
            "name": "",
            "sku": "",
            "unit": "pcs",
            "default_price": "0",
            "description": ""
        }

        for name, control in fields.items():
            value = (
                item.get(name) or ""
                if item is not None
                else defaults[name]
            )
            control.value = "" if value is not None else str(value)
            control.error = None

        editor.visible = True
        list_section.visible = False
        page.update()

    def close_editor(event = None) -> None:
        editor.visible = False
        list_section.visible = True
        page.update()

    def handle_new(event) -> None:
        open_editor()

    def handle_edit(event) -> None:
        try: 
            with closing(connect(database_path)) as connection:
                item = service.get_item(
                    connection,
                    event.control.data,
                )

        except (
            service.CatalogValidationError,
            service.CatalogItemNotFoundError
        ) as error:
            notify(str(error), is_error = True)
            return

        except (sqlite3.Error, OSError):
            logger.exception("Gagal membaca item katalog")
            notify("Item gagal dimuat, Coba lagi dalam beberapa saat.", is_error = True)
            return

    def handle_status(event) -> None:
        item_id, active = event.control.data

        try:
            with closing(connect(database_path)) as connection:
                service.set_item_active(
                    connection,
                    item_id,
                    active
                )
        except (
            service.CatalogItemNotFoundError,
            service.CatalogValidationError,
        ) as error:
            notify(str(error), is_error = True)
            return
        except (sqlite3.Error, OSError) :
            logger.exception("Gagal memuat data katalog.")
            notify(
                "Status item belum berhasil diubah.",
                is_error = True
            )
            return

        refresh_list()
        page.update()

        notify(
            "Item diaktifkan kembali"
            if active
            else "Item diarsipkan."
        )

    def item_card(item: dict) -> ft.Container:
        active = bool(item["is_active"])
        kind = "Produk" if item["type"] == "PRODUCT" else "JASA"
        price = f"{item['default_price']:,}".replace(",", ".")

        details = [
            kind,
            f"SKU {item['sku']}" if item["sku"] else "Tanpa SKU",
            "Aktif" if active else "Diarsipkan"
        ]

        return ft.Container(
            padding = 16,
            border_radius = 8,
            bgcolor = ft.Colors.GREY_100,
            content = ft.Column(
                spacing = 8,
                controls = [
                    ft.Text(
                        item["name"],
                        size = 17,
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(" . ".join(details)),
                    ft.Text(f"Rp{price} / {item['unit']}"),
                    ft.Text(
                        item["description"],
                        visible=bool(item["description"])
                    ),
                    ft.Row(
                        wrap = True,
                        controls = [
                            ft.TextButton(
                                content = "Edit",
                                data = item["id"],
                                on_click = handle_edit,
                            ),
                            ft.TextButton(
                                content = (
                                    "Arsipkan"
                                    if active
                                    else "Aktifkan kembali"
                                ),
                                data = (item["id"], not active),
                                on_click = handle_status,
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
                items = service.list_items(
                    connection,
                    search = current_search,
                    include_archived = include_archived,
                    limit = PAGE_SIZE + 1,
                    offset = offset,
                )

                if not items and offset > 0:
                    offset = 0
                    items = service.list_items(
                        connection,
                        search = current_search,
                        include_archived = include_archived,
                        limit = PAGE_SIZE + 1,
                        offset = offset,
                    )

        except (
            sqlite3.Error,
            OSError,
            service.CatalogValidationError,
        ): 
            logger.exception("Gagal memuat daftar katalog.")
            item_list.controls = [
                ft.Text(
                    "Daftar katalog gagal dimuat. "
                    "Klik Cari untuk mencoba lagi.",
                    color = ft.Colors.RED_700
                )
            ]
            previous_button.disabled = True
            next_button.disabled = True
            page_info.value = ""
            return

        visible_items = items[:PAGE_SIZE]

        item_list.controls = [
            item_card(item) for item in visible_items
        ]

        if not visible_items:
            item_list.controls = [
                ft.Text(
                    "Tidak ada item yang cocok. "
                    "Tambahkan item atau ubah pencarian."
                )
            ]

        previous_button.disabled = True
        next_button.disabled = len(items) <= PAGE_SIZE

        page_info.value = (
            f"Halaman {offset // PAGE_SIZE + 1}"
            f" . {len(visible_items)} item ditampilkan"
        )

    def handle_search(event) -> None:
        nonlocal offset

        offset = 0
        current_search = (search_input.value or "").strip()
        include_archived = bool(archive_checkbox.value)

        refresh_list()
        page.update()

    def handle_page(event) -> None:
        offset = max(0, offset + event.control.data)
        refresh_list()
        page.update()

    def read_form() -> dict | None:
        for control in fields.value():
            control.error = None

        name = (fields["name"].value or "").strip()
        unit = (fields["unit"].value or "").strip()
        raw_price = (fields["default_price"].value or "").strip()

        valid = True
        price = 0

        if not name:
            fields["name"].error = "Nama Produk atau Jasa wajib diisi."
            valid = False

        if not unit:
            fields["unit"].error = "Satuan wajib diisi."
            valid = False

        if re.fullmatch(r"[0-9]+", raw_price) is None:
            fields["default_price"].error = (
                "Gunakan angka utuh, misalnya 150000. "
                "Harga nol diperbolehkan."
            )
            valid = False
        else:
            normalized_price = raw_price.lstrip("0") or "0"

            if len(normalized_price) > 19:
                fields["default_price"].error = "Harga terlalu besar."
                valid = False
            else:
                price = int(normalized_price)

                if price > service.MAX_INTEGER:
                    fields["default_price"].error = (
                        "Harga melampaui batas penyimpanan"
                    )
                    valid = False

        if not valid:
            return None

        return {
            "name": name,
            "sku": fields["sku"].value or "",
            "type": item_type.value or "PRODUCT",
            "unit": unit,
            "default_price": price,
            "description": fields["description"].value or "",
        }

    def handle_save(event) -> None:
        if save_button.disabled:
            return

        data = read_form()

        if data is None:
            page.update()
            return

        save_button.disabled = True
        save_button.content = "Menyimpan..."
        page.update()

        try:
            with closing(connect(database_path)) as connection:
                if editing_id is None:
                    service.create_item(connection, data)
                else:
                    service.update_item(
                        connection,
                        editing_id,
                        data,
                    )

        except (
            service.CatalogValidationError,
            service.CatalogItemNotFoundError,
        ) as error:
            notify(str(error), is_error = True)

        except (sqlite3.Error, OSError):
            logger.exception("Gagal menyimpan item katalog.")
            notify(
                "Item gagal disimpan.",
                is_error = True
            )

        else:
            editor.visible = False
            list_section.visible = True
            refresh_list()
            notify("Item berhasil disimpan.")

        finally:
            save_button.disabled = False
            save_button.content = "Simpan Item"
            page.update()

    save_button = ft.Button(
        content = "Simpan Item",
        on_click = handle_save,
    )

    previous_button = ft.Button(
        content = "Sebelumnya",
        data = -PAGE_SIZE,
        on_click = handle_page,
    )

    next_button = ft.Button(
        content = "Selanjutnya",
        data = PAGE_SIZE,
        on_click = handle_page,
    )

    search_input.on_submit = handle_search 
    archive_checkbox.on_change = handle_search

    editor = ft.Column(
        visible = False,
        spacing = 16,
        controls = [
            form_title,
            ft.ResponsiveRow(
                spacing = 16,
                run_spacing = 16,
                controls = [
                    item_type,
                    *fields.values(),
                ],
            ),
            ft.Row(
                wrap = True,
                controls = [
                    save_button,
                    ft.TextButton(
                        content = "Batal",
                        on_click=close_editor
                    ),
                ],
            ),
        ],
    )

    list_section = ft.Column(
        spacing = 16,
        controls = [
            ft.Button(
                content = "Tambah Item",
                on_click = handle_new,
            ),
            ft.Row(
                controls = [
                    search_input,
                    ft.Button(
                        content = "Cari...",
                        on_click = handle_search,
                    ),
                ],
            ),
            archive_checkbox,
            item_list,
            page_info,
            ft.Row(
                controls = [
                    previous_button,
                    next_button,
                ]
            )
        ]
    )

    refresh_list()

    return ft.Column(
        expand = True,
        scroll = ft.ScrollMode.AUTO,
        spacing = 20,
        controls = [
            ft.Text(
                "Katalog Produk dan Jasa",
                size = 28,
                weight = ft.FontWeight.BOLD,
            ),
            editor,
            list_section,
        ],
    )
