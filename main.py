import logging
import sqlite3

import flet as ft

from app.database import initialize_database
from app.views.business_profile_view import (
    build_business_profile_view,
)
from app.views.customer_view import build_customer_view


logger = logging.getLogger(__name__)


def main(page: ft.Page) -> None:
    page.title = "DokumenUsaha"
    page.window.width = 1000
    page.window.height = 700

    page.padding = 24
    page.theme_mode = ft.ThemeMode.LIGHT

    try:
        database_path = initialize_database()

        profile_view = build_business_profile_view(
            page,
            database_path,
        )

        customer_view = build_customer_view(
            page,
            database_path,
        )

        profile_view.visible = True
        customer_view.visible = False

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
        show_profile = event.control.data == "profile"

        profile_view.visible = show_profile
        customer_view.visible = not show_profile

        profile_button.disabled = show_profile
        customer_button.disabled = not show_profile

        page.update()

    profile_button = ft.TextButton(
        content="Profil Usaha",
        data="profile",
        disabled=True,
        on_click=switch_view,
    )

    customer_button = ft.TextButton(
        content="Pelanggan",
        data="customers",
        on_click=switch_view,
    )

    page.add(
        ft.Column(
            expand=True,
            spacing=16,
            controls=[
                ft.Row(
                    controls=[
                        profile_button,
                        customer_button,
                    ],
                ),
                ft.Divider(),
                profile_view,
                customer_view,
            ],
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ft.run(main)