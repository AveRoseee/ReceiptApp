from contextlib import closing
from datetime import date
import logging
import re
import sqlite3

import flet as ft

from app.database import connect
from app.services import catalog_service, customer_service
from app.services.document_calculator import calculate_document


logger = logging.getLogger(__name__)
MAX_INTEGER = 2**63 - 1


def parse_number(raw, label, digits=0):
    text = (raw or "").strip()
    pattern = r"[0-9]{1,19}"

    if digits:
        pattern += rf"(?:,[0-9]{{1,{digits}}})?"

    if not re.fullmatch(pattern, text):
        raise ValueError(
            f"{label}: masukkan angka tanpa pemisah ribuan."
        )

    whole, _, fraction = text.partition(",")
    value = int(whole) * 10**digits

    if digits:
        value += int(fraction.ljust(digits, "0"))

    if value > MAX_INTEGER:
        raise ValueError(f"{label} terlalu besar.")

    return value


def input_number(value, digits=0):
    if not digits:
        return str(value)

    whole, fraction = divmod(value, 10**digits)
    tail = f"{fraction:0{digits}d}".rstrip("0")

    return str(whole) + ("," + tail if tail else "")


def rupiah(value):
    return "Rp" + f"{value:,}".replace(",", ".")


def load_all(connection, fetch):
    result = []

    while True:
        batch = fetch(
            connection,
            limit=100,
            offset=len(result),
        )
        result.extend(batch)

        if len(batch) < 100:
            return result


class DocumentEditor:
    def __init__(
        self,
        page,
        database_path,
        on_saved,
        on_cancel,
        *,
        service,
        get_document,
        document_label,
        deadline_field,
        deadline_label,
        status_field,
    ):
        self.page = page
        self.database_path = database_path
        self.on_saved = on_saved
        self.service = service
        self.get_document = get_document
        self.document_label = document_label
        self.deadline_field = deadline_field
        self.status_field = status_field
        self.document_id = None
        self.saving = False
        self.rows = []
        self.catalog = {}

        self.title = ft.Text(
            size=22,
            weight=ft.FontWeight.BOLD,
        )
        self.message = ft.Text(color=ft.Colors.RED_700)
        self.summary = ft.Text()
        self.item_list = ft.Column(spacing=12)

        self.customer = ft.Dropdown(
            label="Pelanggan",
            options=[],
        )
        self.issue_date = ft.TextField(
            label="Tanggal (YYYY-MM-DD)",
        )
        self.deadline = ft.TextField(
            label=deadline_label,
        )
        self.catalog_choice = ft.Dropdown(
            label="Item katalog",
            options=[],
        )

        self.discount_type = ft.Dropdown(
            label="Jenis diskon",
            value="AMOUNT",
            options=[
                ft.DropdownOption(
                    key="AMOUNT",
                    text="Nominal rupiah",
                ),
                ft.DropdownOption(
                    key="PERCENT",
                    text="Persentase",
                ),
            ],
            on_select=self.change_discount_type,
        )
        self.discount = ft.TextField(
            label="Diskon (Rp)",
            value="0",
            on_change=self.refresh_totals,
        )
        self.tax = ft.TextField(
            label="Pajak (%)",
            value="0",
            on_change=self.refresh_totals,
        )
        self.notes = ft.TextField(
            label="Catatan",
            multiline=True,
        )
        self.terms = ft.TextField(
            label="Syarat dan ketentuan",
            multiline=True,
        )
        self.save_button = ft.Button(
            content="Simpan Draft",
            on_click=self.save,
        )

        self.control = ft.Column(
            visible=False,
            spacing=16,
            controls=[
                self.title,
                ft.Text(
                    "Harga: angka rupiah tanpa titik. Kuantitas: "
                    "maksimal 3 angka desimal dengan koma, "
                    "misalnya 1,5. Persentase: maksimal "
                    "2 angka desimal."
                ),
                self.customer,
                self.issue_date,
                self.deadline,
                ft.Divider(),
                self.catalog_choice,
                ft.Row(
                    wrap=True,
                    controls=[
                        ft.Button(
                            content="Tambah dari Katalog",
                            on_click=self.add_catalog,
                        ),
                        ft.TextButton(
                            content="Tambah Item Manual",
                            on_click=self.add_manual,
                        ),
                    ],
                ),
                self.item_list,
                ft.Divider(),
                self.discount_type,
                self.discount,
                self.tax,
                self.summary,
                self.notes,
                self.terms,
                self.message,
                ft.Row(
                    controls=[
                        self.save_button,
                        ft.TextButton(
                            content="Batal",
                            on_click=on_cancel,
                        ),
                    ],
                ),
            ],
        )

    def open(self, document_id=None):
        with closing(connect(self.database_path)) as connection:
            draft = (
                self.get_document(connection, document_id)
                if document_id is not None
                else None
            )

            if draft and (
                draft[self.status_field] != "DRAFT"
                or draft["number"] is not None
            ):
                raise ValueError(
                    "Hanya draft yang belum diterbitkan "
                    "dapat diedit."
                )

            customers = load_all(
                connection,
                customer_service.list_customers,
            )
            catalog = load_all(
                connection,
                catalog_service.list_items,
            )

            missing_customer = None

            if draft and not any(
                customer["id"] == draft["customer_id"]
                for customer in customers
            ):
                missing_customer = customer_service.get_customer(
                    connection,
                    draft["customer_id"],
                )

        self.document_id = document_id
        self.catalog = {
            item["id"]: item
            for item in catalog
        }

        self.customer.options = [
            ft.DropdownOption(
                key=str(customer["id"]),
                text=customer["name"],
            )
            for customer in customers
        ]

        if missing_customer:
            self.customer.options.append(
                ft.DropdownOption(
                    key=str(missing_customer["id"]),
                    text=(
                        missing_customer["name"]
                        + " (diarsipkan; pilih pelanggan aktif)"
                    ),
                )
            )

        self.catalog_choice.options = [
            ft.DropdownOption(
                key=str(item["id"]),
                text=(
                    f"{item['name']} — "
                    f"{rupiah(item['default_price'])}/{item['unit']}"
                ),
            )
            for item in catalog
        ]

        self.catalog_choice.value = None
        self.customer.value = (
            str(draft["customer_id"]) if draft else None
        )
        self.issue_date.value = (
            draft["issue_date"]
            if draft
            else date.today().isoformat()
        )
        self.deadline.value = (
            (draft[self.deadline_field] or "") if draft else ""
        )
        self.discount_type.value = (
            draft["discount_type"] if draft else "AMOUNT"
        )
        self.discount.label = (
            "Diskon (%)"
            if self.discount_type.value == "PERCENT"
            else "Diskon (Rp)"
        )
        self.discount.value = input_number(
            draft["discount_value"] if draft else 0,
            2 if self.discount_type.value == "PERCENT" else 0,
        )
        self.tax.value = input_number(
            draft["tax_rate_bps"] if draft else 0,
            2,
        )
        self.notes.value = draft["notes"] if draft else ""
        self.terms.value = draft["terms"] if draft else ""
        self.title.value = (
            f"Edit Draft {self.document_label} #{document_id}"
            if draft
            else f"Tambah {self.document_label}"
        )

        self.message.value = (
            "Pelanggan diarsipkan. Pilih pelanggan aktif "
            "sebelum menyimpan."
            if missing_customer
            else "Tambahkan pelanggan aktif di halaman "
            "Pelanggan terlebih dahulu."
            if not customers
            else ""
        )

        self.rows.clear()
        self.item_list.controls.clear()

        for item in draft["items"] if draft else []:
            self.add_row(item)

        self.refresh_totals(update=False)

    def add_row(self, item):
        fields = {
            "name_snapshot": ft.TextField(
                label="Nama item",
                value=item.get("name_snapshot", ""),
            ),
            "description": ft.TextField(
                label="Deskripsi",
                value=item.get("description", ""),
            ),
            "quantity_milli": ft.TextField(
                label="Kuantitas",
                value=input_number(
                    item.get("quantity_milli", 1000),
                    3,
                ),
            ),
            "unit": ft.TextField(
                label="Satuan",
                value=item.get("unit", ""),
            ),
            "unit_price": ft.TextField(
                label="Harga satuan (Rp)",
                value=str(item.get("unit_price", 0)),
            ),
        }

        for field in fields.values():
            field.on_change = self.refresh_totals

        total = ft.Text()
        catalog_id = item.get("catalog_item_id")
        source = (
            "Item manual"
            if catalog_id is None
            else f"Item katalog #{catalog_id}"
        )

        if (
            catalog_id is not None
            and catalog_id not in self.catalog
        ):
            source += (
                " (diarsipkan; hapus baris atau "
                "aktifkan kembali di Katalog)"
            )

        row = {
            "catalog_item_id": catalog_id,
            "fields": fields,
            "total": total,
        }

        def remove(event):
            self.rows.remove(row)
            self.item_list.controls.remove(row["control"])
            self.refresh_totals()

        row["control"] = ft.Container(
            padding=12,
            border_radius=8,
            bgcolor=ft.Colors.GREY_100,
            content=ft.Column(
                controls=[
                    ft.Text(source),
                    *fields.values(),
                    total,
                    ft.TextButton(
                        content="Hapus Item",
                        on_click=remove,
                    ),
                ],
            ),
        )

        self.rows.append(row)
        self.item_list.controls.append(row["control"])

    def add_manual(self, event):
        self.add_row({})
        self.refresh_totals()

    def add_catalog(self, event):
        selected = self.catalog.get(
            int(self.catalog_choice.value or "0")
        )

        if selected is None:
            self.message.value = (
                "Pilih item katalog terlebih dahulu."
            )
            self.page.update()
            return

        self.add_row(
            {
                "catalog_item_id": selected["id"],
                "name_snapshot": selected["name"],
                "description": selected["description"],
                "quantity_milli": 1000,
                "unit": selected["unit"],
                "unit_price": selected["default_price"],
            }
        )

        self.message.value = ""
        self.refresh_totals()

    def read_items(self):
        result = []

        for position, row in enumerate(self.rows, 1):
            fields = row["fields"]

            result.append(
                {
                    "catalog_item_id": row["catalog_item_id"],
                    "name_snapshot": (
                        fields["name_snapshot"].value or ""
                    ),
                    "description": (
                        fields["description"].value or ""
                    ),
                    "unit": fields["unit"].value or "",
                    "quantity_milli": parse_number(
                        fields["quantity_milli"].value,
                        f"Kuantitas item {position}",
                        3,
                    ),
                    "unit_price": parse_number(
                        fields["unit_price"].value,
                        f"Harga item {position}",
                    ),
                }
            )

        return result

    def read_options(self):
        return {
            "discount_type": self.discount_type.value,
            "discount_value": parse_number(
                self.discount.value,
                "Diskon",
                2 if self.discount_type.value == "PERCENT" else 0,
            ),
            "tax_rate_bps": parse_number(
                self.tax.value,
                "Pajak",
                2,
            ),
        }

    def refresh_totals(self, event=None, *, update=True):
        for row in self.rows:
            row["total"].value = ""

        try:
            totals = calculate_document(
                self.read_items(),
                **self.read_options(),
            )
        except ValueError as error:
            self.summary.value = (
                f"Total belum dapat dihitung: {error}"
            )
        else:
            for row, total in zip(
                self.rows,
                totals["line_totals"],
            ):
                row["total"].value = (
                    f"Total item: {rupiah(total)}"
                )

            self.summary.value = (
                f"Subtotal: {rupiah(totals['subtotal'])}\n"
                f"Diskon: {rupiah(totals['discount_amount'])}\n"
                f"Pajak: {rupiah(totals['tax_amount'])}\n"
                f"Total: {rupiah(totals['grand_total'])}"
            )

        if update:
            self.page.update()

    def change_discount_type(self, event):
        self.discount.label = (
            "Diskon (%)"
            if self.discount_type.value == "PERCENT"
            else "Diskon (Rp)"
        )
        self.discount.value = "0"
        self.refresh_totals()

    def save(self, event):
        if self.saving:
            return

        self.saving = True
        self.save_button.disabled = True
        self.message.value = ""
        self.page.update()
        saved = None

        try:
            if not self.customer.value:
                raise ValueError(
                    "Pilih pelanggan terlebih dahulu."
                )

            data = {
                "customer_id": int(self.customer.value),
                "issue_date": (
                    self.issue_date.value or ""
                ).strip(),
                self.deadline_field: (
                    self.deadline.value or ""
                ).strip() or None,
                "notes": self.notes.value or "",
                "terms": self.terms.value or "",
                "items": self.read_items(),
                **self.read_options(),
            }

            with closing(connect(self.database_path)) as connection:
                if self.document_id is None:
                    saved = self.service.create_draft(
                        connection,
                        data,
                    )
                else:
                    saved = self.service.update_draft(
                        connection,
                        self.document_id,
                        data,
                    )

            self.document_id = saved["id"]

        except (ValueError, LookupError) as error:
            self.message.value = str(error)

        except (sqlite3.Error, OSError):
            logger.exception("Gagal menyimpan draft %s", self.document_label)
            self.message.value = (
                "Draft belum tersimpan. Silakan coba kembali."
            )

        finally:
            self.saving = False
            self.save_button.disabled = False
            self.page.update()

        if saved is not None:
            self.on_saved(saved)