from contextlib import closing
import logging
import sqlite3
import flet as ft
from app.database import connect
from app.services.dashboard_service import get_dashboard
from app.components.design import panel, INK, MUTED, PRIMARY, BACKGROUND, BORDER, SURFACE

logger = logging.getLogger(__name__)


def build_dashboard_view(page, database_path, navigate):
    body = ft.Column(spacing=24, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    money = lambda value: "Rp" + f"{value:,}".replace(",", ".")

    def metric(label, value, icon):
        return panel(ft.Column(spacing=12, controls=[
            ft.Row(controls=[ft.Text(label, color=MUTED, size=13, expand=True),
                             ft.Icon(icon, size=18, color=PRIMARY)]),
            ft.Text(str(value), size=25, weight=ft.FontWeight.W_600, color=INK),
        ]), padding=20, col={"xs": 12, "sm": 6, "xl": 3})

    def refresh(event=None):
        try:
            with closing(connect(database_path)) as connection:
                data = get_dashboard(connection)
            rows = []
            for row in data["due_invoices"]:
                rows.append(ft.DataRow(on_select_change=lambda e, id=row["id"]: navigate("invoices", id), cells=[
                    ft.DataCell(ft.Text(row["number"], color=PRIMARY, weight=ft.FontWeight.W_600)),
                    ft.DataCell(ft.Text(row["customer_name"])),
                    ft.DataCell(ft.Text(row["due_date"])),
                    ft.DataCell(ft.Text(money(row["balance_due"]), weight=ft.FontWeight.W_600)),
                ]))
            due = ft.Row(scroll=ft.ScrollMode.AUTO, controls=[ft.DataTable(
                show_checkbox_column=False, heading_row_color=BACKGROUND,
                heading_row_height=46, data_row_min_height=58, column_spacing=36,
                columns=[ft.DataColumn(ft.Text(label, color=MUTED), numeric=label == "Sisa tagihan")
                         for label in ("Invoice", "Pelanggan", "Jatuh tempo", "Sisa tagihan")], rows=rows,
            )]) if rows else ft.Container(padding=ft.Padding.symmetric(vertical=32), content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12, controls=[
                    ft.Icon(ft.Icons.EVENT_AVAILABLE_OUTLINED, size=32, color=PRIMARY),
                    ft.Text("Tidak ada invoice mendekati jatuh tempo.", color=MUTED),
                ]))
            if rows:
                table = due.controls[0]
                table.width = max(600, (getattr(page, "width", None) or 1280) - 332)
                def fit_table(event):
                    table.width = max(600, event.width)
                    table.update()
                due.on_size_change = fit_table
            body.controls = [
                ft.ResponsiveRow(spacing=16, run_spacing=16, controls=[
                    metric("Tagihan belum diterima", money(data["outstanding"]), ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED),
                    metric("Invoice jatuh tempo", data["overdue_count"], ft.Icons.EVENT_OUTLINED),
                    metric("Draft invoice", data["draft_count"], ft.Icons.EDIT_NOTE_OUTLINED),
                    metric("Pembayaran bulan ini", money(data["month_received"]), ft.Icons.PAYMENTS_OUTLINED),
                ]),
                panel(ft.Column(spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
                    ft.Row(wrap=True, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[
                        ft.Column(spacing=4, controls=[
                            ft.Text("Jatuh tempo terdekat", size=20, weight=ft.FontWeight.W_600),
                            ft.Text("Invoice belum lunas hingga 7 hari ke depan", size=13, color=MUTED)]),
                        ft.TextButton(content="Muat Ulang", icon=ft.Icons.REFRESH, on_click=refresh),
                    ]), due,
                ])),
            ]
        except (ValueError, sqlite3.Error, OSError):
            logger.exception("Gagal membaca dashboard")
            body.controls = [panel(ft.Column(controls=[
                ft.Text("Ringkasan belum dapat dimuat. Klik Muat Ulang."),
                ft.TextButton(content="Muat Ulang", on_click=refresh)]))]
        if event is not None:
            page.update()

    view = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=28,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
            ft.Row(wrap=True, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[
                ft.Column(spacing=6, controls=[ft.Text("Dashboard", size=30, weight=ft.FontWeight.W_600),
                    ft.Text("Pantau tagihan dan pembayaran usaha Anda.", color=MUTED)]),
                ft.Button(content="Buat Invoice", icon=ft.Icons.ADD, on_click=lambda e: navigate("invoices", "new")),
            ]), body,
            ft.Text("Akses cepat", size=20, weight=ft.FontWeight.W_600),
            ft.Row(wrap=True, spacing=12, controls=[
                ft.Button(style=ft.ButtonStyle(bgcolor=SURFACE, color=PRIMARY, side=ft.BorderSide(1, BORDER)), content="Tambah Pelanggan", icon=ft.Icons.PERSON_ADD_OUTLINED,
                          on_click=lambda e: navigate("customers", "new")),
                ft.Button(style=ft.ButtonStyle(bgcolor=SURFACE, color=PRIMARY, side=ft.BorderSide(1, BORDER)), content="Catat Pembayaran", icon=ft.Icons.PAYMENTS_OUTLINED,
                          on_click=lambda e: navigate("invoices", "payments")),
            ]),
        ])
    refresh()
    view.data = refresh
    return view
