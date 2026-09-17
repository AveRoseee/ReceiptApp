import logging
import sqlite3

import flet as ft

from app.database import initialize_database
from app.views.business_profile_view import (
    build_business_profile_view
)


logger = logging.getLogger(__name__)


def main(page: ft.Page):
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
    except (sqlite3.Error, OSError, ValueError):
        logger.exception("Gagal menyiapkan aplikasi")

        page.add(
            ft.Column(
                spacing=12,
                controls=[
                    ft.Text(
                        "Aplikasi belum dapat dibuka",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        "Data usaha belum berhasil dimuat. "
                        "Tutup aplikasi dan periksa detail kesalahan "
                        "pada terminal sebelum mencoba kembali"
                    ),
                ],
            )
        )

        return

    page.add(profile_view)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ft.run(main)