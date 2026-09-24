import logging
import sqlite3

import flet as ft

from app.database import initialize_database
from app.views.business_profile_view import build_business_profile_view
from app.views.customer_view import build_customer_view
from app.views.catalog_view import build_catalog_view
from app.views.quotation_view import build_quotation_view
from app.views.invoice_view import build_invoice_view


logger = logging.getLogger(__name__)


def main(page: ft.Page) -> None:
    page.title = "DokumenUsaha"
    page.window.width = 1000
    page.window.height = 700

    page.padding = 24
    page.theme_mode = ft.ThemeMode.LIGHT

    try:
        database_path = initialize_database()

        views = {
            "profile": build_business_profile_view(
                page,
                database_path,
            ),

            "customers": build_customer_view(
                page,
                database_path,
            ),

            "catalog": build_catalog_view(
                page,
                database_path,
            ),

            "invoices": build_invoice_view(
                page,
                database_path,
            ),

            "quotations": build_quotation_view(
                page,
                database_path
            )
        }
        
    except (sqlite3.Error, OSError, ValueError):
        logger.exception("Gagal menyiapkan aplikasi")

        page.add(
            ft.Text(
                "Aplikasi belum dapat dibuka. "
                "Periksa detail kesalahan pada terminal.",
                color=ft.Colors.RED_700,
            )
        )
        return

    def switch_view(event) -> None:
        selected = event.control.data

        for key, view in views.items():
            view.visible = key == selected
            navigation_buttons[key].disabled = key == selected

        refresh = views[selected].data
        if callable(refresh):
            refresh()

        page.update()

    labels = {
        "profile": "Profil Usaha",
        "customers": "Pelanggan",
        "catalog": "Katalog",
        "invoices": "Invoice",
        "quotations": "Penawaran",
    }

    navigation_buttons = {
        key: ft.TextButton(
            content = label,
            data = key,
            disabled = key == "profile",
            on_click = switch_view,
        )
        for key, label in labels.items()
    }

    for key, view in views.items():
        view.visible = key == "profile"

    page.add(
        ft.Column(
            expand = True,
            spacing = 16,
            controls = [
                ft.Row(
                    wrap = True,
                    controls = list(navigation_buttons.values()), 
                ),
                ft.Divider(),
                *views.values(),
            ]
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ft.run(main)