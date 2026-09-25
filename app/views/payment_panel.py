from contextlib import closing
from datetime import date
import logging
import sqlite3
from uuid import uuid4

import flet as ft


from app.database import connect
from app.services import payment_service as service
from app.views.document_editor import parse_number


logger = logging.getLogger(__name__)

PAGE_SIZE = 20

METHODS = {
    "CASH": "Tunai",
    "TRANSFER": "Transfer",
    "QRIS": "QRIS",
    "OTHER": "Lainnya",
}

SETTLEMENT_LABELS = {
    "UNPAID": "Belum dibayar",
    "PARTIAL": "Dibayar sebagian",
    "PAID": "Lunas"
}


def rupiah(value):
    return "Rp" + f"{value:,}".replace(",", ".")


class PaymentPanel:
    def __init__(self, page, database_path):
        self.page = page
        self.database_path = database_path
        self.invoice_id = None
        self.summary = None
        self.offset = 0
        self.saving = False
        self.request_id = None

        self.pending = {}
        self.void_dialog = None

        self.heading = ft.Text(
            "Pembayaran",
            size = 20,
            weight = ft.FontWeight.BOLD,
        )

        self.balance = ft.Text()
        self.load_error = ft.Text(color = ft.Colors.RED_700)
        self.form_error = ft.Text(color = ft.Colors.RED_700)
        self.history = ft.Column(spacing = 20)
        self.page_info = ft.Text()

        self.payment_date = ft.TextField(
            label = "Tanggal pembayaran (YYYY-MM-DD)",
        )
        self.amount = ft.TextField(
            label = "Nominal pembayaran (Rp)",
            hint_text = "Contoh: 300000, tanpa titik atau koma",
        )
        self.method = ft.Dropdown(
            label = "Metode Pembayaran",
            value = "TRANSFER",
            options = [
                ft.DropdownOption(key = key, text = label)
                for key, label in METHODS.items()
            ],
        )
        self.reference = ft.TextField(
            label = "Referensi pembayaran (Opsional)",
        )
        self.notes = ft.TextField(
            label = "Catatan pembayaran (Opsional)",
            multiline = True
        )

        self.inputs = (
            self.payment_date,
            self.amount,
            self.method,
            self.reference,
            self.notes,
        )

        self.save_button = ft.TextButton(
            content = "Simpan Pembayaran",
            on_click = self.save,
        )
        self.cancel_button = ft.TextButton(
            content = "Tutup Form Pembayaran",
            on_click = self.close_form,
        )

        self.form = ft.Column(
            visible = False,
            spacing = 12,
            controls = [
                ft.Text("Catat pembayaran yang sudah diterima."),
                *self.inputs,
                self.form_error,
                ft.Row(
                    wrap = True,
                    controls = [
                        self.save_button,
                        self.cancel_button,
                    ]
                )
            ]
        )

        self.add_button = ft.Button(
            content = "Catat Pembayaran",
            on_click = self.open_form,
        )

        self.retry_button = ft.TextButton(
            content = "Muat Ulang Pembayaran",
            on_click = self.handle_reload,
        )

        self.include_void = ft.Checkbox(
            label = "Sertakan pembayaran yang dibatalkan.",
            value = True,
            on_change = self.handle_filter,
        )

        self.previous = ft.TextButton(
            content = "Riwayat sebelumnya",
            data = -PAGE_SIZE,
            on_click = self.handle_page,
        )

        self.next = ft.TextButton(
            content = "Riwayat berikutnya",
            data = PAGE_SIZE,
            on_click = self.handle_page,
        )

        self.control = ft.Column(
            visible = False,
            spacing = 12,
            controls = [
                ft.Divider(),
                self.heading,
                self.balance,
                self.load_error,
                ft.Row(
                    wrap = True,
                    controls = [
                        self.add_button,
                        self.retry_button,
                    ],
                ),
                self.form,
                ft.Text(
                    "Riwayat Pembayaran",
                    weight = ft.FontWeight.BOLD,
                ),
                self.include_void,
                self.history,
                self.page_info,
                ft.Row(
                    wrap = True,
                    controls = [
                        self.previous,
                        self.next
                    ],
                ),
            ],
        )

    def notify(self, message):
        self.page.show_dialog(
            ft.SnackBar(
                content = ft.Text(message),
            )
        )

    def load(self, invoice_id, *, reset = True):
        if reset or invoice_id != self.invoice_id:
            self.offset = 0
            self.form.visible = False

        self.invoice_id = invoice_id
        self.load_error.value = ""

        try:
            with closing(connect(self.database_path)) as connection:
                connection.execute("BEGIN")

                summary = service.get_invoice_summary(
                    connection,
                    invoice_id,
                )

                rows = service.list_payments(
                    connection,
                    invoice_id,
                    include_void = bool(self.include_void.value),
                    limit = PAGE_SIZE + 1,
                    offset = self.offset,
                )

                if not rows and self.offset:
                    self.offset = 0

                    rows = service.list_payments(
                        connection,
                        invoice_id,
                        include_void = bool(self.include_void.value),
                        limit = PAGE_SIZE + 1,
                        offset = 0
                    )

        except (ValueError, LookupError, sqlite3.Error, OSError):
            logger.exception(
                "Gagal memuat pembayaran invoice %s",
                invoice_id,
            )

            self.summary = None
            self.control.visible = True
            self.balance.value = ""
            self.history.controls = []
            self.page_info.value = ""
            self.add_button.disabled = True
            self.previous.disabled = True
            self.next.disabled = True
            self.load_error.value = (
                "Pembayaran belum dapat dimuat. Silahkan muat ulang."
            )
            return False

        self.summary = summary
        self.control.visible = summary["document_status"] != "DRAFT"

        label = SETTLEMENT_LABELS[summary["settlement_status"]]

        if summary["document_status"] == "CANCELLED":
            label = "Invoice dibatalkan"

        overdue = (
            "\nMelewati tanggal jatuh Tempo."
            if summary["is_overdue"]
            else ""
        )

        self.balance.value = (
            f"Total Balance: {rupiah(summary['grand_total'])}\n"
            f"Sudah dibayar: {rupiah(summary['paid_amount'])}\n"
            f"Sisa tagihan: {rupiah(summary['balance_due'])}\n"
            f"Status pembayaran: {label}{overdue}"
        )

        can_pay = (
            summary["document_status"] == "ISSUED"
            and summary["balance_due"] > 0
        )

        can_retry = invoice_id in self.pending

        self.add_button.visible = can_pay or can_retry
        self.add_button.disabled = self.saving

        visible = rows[:PAGE_SIZE]

        self.history.controls = [
            self.payment_card(row)
            for row in visible
        ]

        if not visible:
            self.history.controls = [
                ft.Text("Belum ada pembayaran yang sesuai filter.")
            ]

        self.previous.disabled = self.offset == 0
        self.next.disabled = len(rows) <= PAGE_SIZE

        self.page_info.value = (
            f"Halaman riwayat {self.offset // PAGE_SIZE + 1}"
            f" · {len(visible)} pembayaran"
        )

        return True

    def refresh(self):
        if self.invoice_id is not None:
            return self.load(
                self.invoice_id,
                reset = False,
            )

        return False

    def handle_reload(self, event):
        self.refresh()
        self.page.update()

    def handle_filter(self, event):
        self.offset = 0
        self.handle_reload(event)

    def handle_page(self, event):
        self.offset = max(
            0,
            self.offset + event.control.data,
        )
        self.handle_reload(event)

    def payment_card(self, payment):
        active = payment["status"] == "VALID"

        return ft.Container(
            padding = 12,
            border_radius = 8,
            bgcolor = ft.Colors.GREY_100,
            content = ft.Column(
                spacing = 6,
                controls = [
                    ft.Text(
                        f"Pembayaran #{payment['id']} · " 
                        f"{rupiah(payment['amount'])}",
                        weight = ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        f"{payment['payment_date']} · "
                        f"{METHODS[payment['method']]}"
                    ),
                    ft.Text(
                        "Aktif" if active else "Dibatalkan"
                    ),
                    ft.Text(
                        payment["reference"],
                        visible = bool(payment["reference"])
                    ),
                    ft.Text(
                        payment["notes"],
                        visible = bool(payment["notes"]),
                    ),
                    ft.Text(
                        f"Alasan: {payment['void_reason'] or "-"}",
                        visible = not active
                    ),
                    ft.TextButton(
                        content = "Batalkan Pembayaran",
                        data = payment["id"],
                        visible = active,
                        on_click = self.ask_void,
                    ),
                ],
            ),
        )

    def lock_input(self, locked):
        for control in self.inputs:
            control.disabled = locked

    def open_form(self, event):
        if (
            self.saving
            or self.form.visible
            or self.invoice_id is None
        ):
            return

        if not self.refresh():
            self.page.update()
            return

        pending = self.pending.get(self.invoice_id)

        if not pending and not (
            self.summary["document_status"] == "ISSUED"
            and self.summary["balance_due"] > 0
        ):
            self.page.update()
            return

        self.request_id = (
            pending["request_id"]
            if pending
            else str(uuid4())
        )

        data = (
            pending["data"]
            if pending
            else {
                "payment_date": date.today().isoformat(),
                "amount": "",
                "method": "TRANSFER",
                "reference": "",
                "notes": "",
            }
        )

        keys = (
            "payment_date",
            "amount",
            "method",
            "reference",
            "notes",
        )

        for control, key in zip(self.inputs, keys):
            control.value = str(data[key])

        self.lock_input(pending is not None)

        self.form_error.value = (
            "Coba simpan kembali untuk memeriksa penyimpanan sebelumnya."
            if pending
            else ""
        )

        self.form.visible = True
        self.page.update()

    def close_form(self, event):
        if self.saving:
            return

        self.form.visible = False
        self.form_error.value = ""
        self.page.update()

    def save(self, event):
        if self.saving or not self.form.visible:
            return

        invoice_id = self.invoice_id

        self.saving = True
        self.save_button.disabled = True
        self.cancel_button.disabled = True
        self.form_error.value = ""
        self.page.update()

        result = None

        try:
            pending = self.pending.get(invoice_id)

            if pending is None:
                payload = {
                    "payment_date": (
                        self.payment_date.value or ""
                    ).strip(),
                    "amount": parse_number(
                        self.amount.value,
                        "Nominal Pembayaran",
                    ),
                    "method": self.method.value,
                    "reference": self.reference.value or "",
                    "notes": self.notes.value or "",
                }

                pending = {
                    "request_id": self.request_id,
                    "data": payload,
                }

                self.pending[invoice_id] = pending

            self.lock_input(True)

            with closing(connect(self.database_path)) as connection:
                result = service.record_payment(
                    connection,
                    invoice_id,
                    pending["data"],
                    request_id = pending["request_id"],
                )

        except (ValueError, LookupError) as error:
            self.pending.pop(invoice_id, None)
            self.lock_input(False)
            self.form_error.value = str(error)

        except (sqlite3.Error, OSError, RuntimeError):
            logger.exception(
                "Gagal menyimpan pembayaran saldo %s",
                invoice_id,
            )

            self.form_error.value = (
                "Penyimpanan belum dapat dipastikan. "
                "Klik 'Simpan Pembayaran' untuk mencoba kembali "
                "dengan data yang sama."
            )

        finally:
            self.saving = False
            self.save_button.disabled = False
            self.cancel_button.disabled = False

        if result is not None:
            self.pending.pop(invoice_id, None)
            self.form.visible = False
            self.lock_input(False)
            self.offset = 0

            self.refresh()
            self.notify("Pembayaran berhasil dicatat.")

        self.page.update()

    def ask_void(self, event):
        if (
            self.void_dialog is not None
            and self.void_dialog.open
        ): 
            return

        payment_id = event.control.data

        reason = ft.TextField(
            label = "Alasan pembatalan",
            multiline = True,
        )
        message = ft.Text(color = ft.Colors.RED_700)

        finished = False
        busy = False

        def dismiss(event):
            nonlocal finished
            finished = True

        def cancel(event):
            if busy or finished:
                return

            dismiss(event)
            self.page.pop_dialog()

        def confirm(event):
            nonlocal busy, finished

            if busy or finished:
                return

            reason.error = None
            message.value = ""

            if not (reason.value or "").strip():
                reason.error = "Isi alasan pembatalan."
                self.page.update()
                return

            busy = True
            confirm_button.disabled = True
            cancel_button.disabled = True
            self.page.update()

            success = False

            try:
                with closing(connect(self.database_path)) as connection:
                    service.void_payment(
                        connection,
                        payment_id,
                        reason.value,
                    )

                success = True

            except (ValueError, LookupError) as error:
                message.value = str(error)

            except (sqlite3.Error, OSError, RuntimeError):
                logger.exception(
                    "Gagal membatalkan pembayaran %s",
                    payment_id,
                )
                message.value = (
                    "Pembayaran belum dapat dibatalkan. "
                    "Silahkan coba kembali."
                )

            finally:
                busy = False 
                confirm_button.disabled = False
                cancel_button.disabled = False

            if success:
                finished = True
                self.page.pop_dialog()
                self.refresh()
                self.notify(
                    "Pembayaran dibatalkan dan saldo diperbarui."
                )


            self.page.update()

        cancel_button = ft.TextButton(
            content = "Kembali",
            on_click = cancel,
        )
        confirm_button = ft.TextButton(
            content = "Ya, Batalkan Pembayaran",
            on_click = confirm,
        )

        self.void_dialog = ft.AlertDialog(
            modal = True,
            title = ft.Text(
                f"Batalkan pembayaran #{payment_id}?"
            ),
            scrollable = True,
            content = ft.Column(
                tight = True,
                controls = [
                    ft.Text(
                        "Pembayaran ini tidak lagi mengurangi sisa tagihan. "
                        "Kwitansi terkait juga dibatalkan. "
                        "Riwayat tetap disimpan."
                    ),
                    reason,
                    message,
                ],
            ),
            actions = [
                cancel_button,
                confirm_button,
            ],
            on_dismiss = dismiss,
        )

        self.page.show_dialog(self.void_dialog)