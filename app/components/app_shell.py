import flet as ft
from app.components.design import BACKGROUND, SURFACE, BORDER, INK, MUTED, PRIMARY, navigation_style, polish


class AppShell:
    def __init__(self, page, views, on_select):
        entries = [
            ("dashboard", "Dashboard", ft.Icons.SPACE_DASHBOARD_OUTLINED),
            ("invoices", "Invoice", ft.Icons.RECEIPT_LONG_OUTLINED),
            ("quotations", "Penawaran", ft.Icons.DESCRIPTION_OUTLINED),
            ("customers", "Pelanggan", ft.Icons.PEOPLE_OUTLINED),
            ("catalog", "Katalog", ft.Icons.INVENTORY_2_OUTLINED),
            ("profile", "Profil Usaha", ft.Icons.STOREFRONT_OUTLINED),
            ("settings", "Pengaturan", ft.Icons.SETTINGS_OUTLINED),
        ]
        self.buttons = {key: ft.TextButton(content=label, icon=icon, data=key,
            disabled=key == "profile", width=194, height=46, style=navigation_style(key == "profile"),
            on_click=on_select) for key, label, icon in entries}
        self.menu = ft.Column(spacing=6, controls=list(self.buttons.values()))
        self.compact_menu = ft.Row(scroll=ft.ScrollMode.AUTO, visible=False)
        sidebar = ft.Container(width=226, bgcolor=SURFACE, padding=16,
            border=ft.Border(right=ft.BorderSide(1, BORDER)), content=ft.Column(expand=True, controls=[
                ft.Container(padding=ft.Padding.only(top=12, bottom=30),
                    content=ft.Text("DokumenUsaha", size=23, weight=ft.FontWeight.W_700, color=INK)),
                self.menu, ft.Container(expand=True), ft.Divider(height=1),
                ft.Row(controls=[ft.Icon(ft.Icons.LAPTOP_OUTLINED, size=18, color=MUTED),
                    ft.Text("Tersimpan di perangkat", size=12, color=MUTED)]),
            ]))
        for key, view in views.items():
            view.visible = key == "profile"
            view.expand = True
            view.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
            polish(view)
        self.content = ft.Container(expand=True, padding=28, bgcolor=BACKGROUND,
            content=ft.Column(expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                              controls=list(views.values())))
        self.control = ft.Column(expand=True, spacing=0, controls=[self.compact_menu,
            ft.Row(expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                   controls=[sidebar, self.content])])
        self.compact = None

        def resize(event=None):
            compact = (getattr(page, "width", None) or 1280) < 900
            if compact != self.compact:
                self.compact = compact
                self.menu.controls = [] if compact else list(self.buttons.values())
                self.compact_menu.controls = list(self.buttons.values()) if compact else []
                sidebar.visible = not compact
                self.compact_menu.visible = compact
                self.content.padding = 16 if compact else 28
                for button in self.buttons.values():
                    button.width = None if compact else 194
                if event is not None:
                    page.update()
        page.on_resize = resize
        resize()

    def select(self, key):
        for name, button in self.buttons.items():
            button.disabled = name == key
            button.style = navigation_style(name == key)
