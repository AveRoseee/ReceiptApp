import flet as ft


def main(page: ft.Page):
    page.title = "DokumenUsaha"
    page.window.width = 1000
    page.window.height = 700

    page.add(
        ft.Text(
            "Dokumen Usaha",
            size=32,
            weight=ft.FontWeight.BOLD,
        ),
        ft.Text(
            "Environment berhasil dijalankan."
        )
    )


ft.run(main)