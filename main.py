import logging
import sqlite3

import flet as ft

from app.database import initialize_database
from app.components.app_shell import AppShell
from app.components.design import theme, BACKGROUND, polish
from app.views.business_profile_view import build_business_profile_view
from app.views.customer_view import build_customer_view
from app.views.catalog_view import build_catalog_view
from app.views.quotation_view import build_quotation_view
from app.views.invoice_view import build_invoice_view
from app.views.settings_view import build_settings_view
from app.views.dashboard_view import build_dashboard_view


logger = logging.getLogger(__name__)


def main(page: ft.Page) -> None:
    page.title = "DokumenUsaha"
    page.window.width = 1280
    page.window.height = 850

    page.padding = 0
    page.bgcolor = BACKGROUND
    page.theme = theme()
    page.theme_mode = ft.ThemeMode.LIGHT

    try:
        database_path = initialize_database()

        def reload_after_restore():
            page.controls.clear()
            page.services.clear()
            main(page)
            page.update()

        def navigate(key, action=None):
            from types import SimpleNamespace
            switch_view(SimpleNamespace(control=SimpleNamespace(data=key)))
            handler = getattr(views[key].data, "navigate", None)
            if handler:
                handler(action)
            page.update()

        views = {
            "dashboard": build_dashboard_view(page, database_path, navigate),
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
            ),
            "settings": build_settings_view(page, database_path, reload_after_restore),
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

        polish(views[selected])
        shell.select(selected)
        page.update()

    shell = AppShell(page, views, switch_view)
    navigation_buttons = shell.buttons
    page.add(shell.control)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ft.run(main)