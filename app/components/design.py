"""Shared visual language; controls retain their values and handlers."""
import flet as ft

BACKGROUND = "#F5F7F8"
SURFACE = "#FFFFFF"
INK = "#172B26"
MUTED = "#65736E"
PRIMARY = "#176B52"
TINT = "#E8F3EE"
BORDER = "#E3E9E6"


def theme():
    return ft.Theme(use_material3=True, color_scheme_seed=PRIMARY, font_family="Segoe UI",
        scaffold_bgcolor=BACKGROUND, divider_color=BORDER,
        color_scheme=ft.ColorScheme(primary=PRIMARY, on_primary=SURFACE,
            surface=SURFACE, on_surface=INK, on_surface_variant=MUTED,
            outline=BORDER, outline_variant=BORDER, surface_tint=SURFACE,
            primary_container=TINT, on_primary_container=PRIMARY),
        text_theme=ft.TextTheme(body_medium=ft.TextStyle(size=14, color=INK),
            body_large=ft.TextStyle(size=15, color=INK), body_small=ft.TextStyle(size=12, color=MUTED),
            label_large=ft.TextStyle(size=14, weight=ft.FontWeight.W_600)),
        button_theme=ft.ButtonTheme(style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: PRIMARY, ft.ControlState.DISABLED: BORDER},
            color={ft.ControlState.DEFAULT: SURFACE, ft.ControlState.DISABLED: MUTED},
            elevation=0, shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(horizontal=20, vertical=15))),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(color=PRIMARY,
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12))))


def panel(content, *, padding=24, **kwargs):
    return ft.Container(content=content, bgcolor=SURFACE, padding=padding,
        border=ft.Border.all(1, BORDER), border_radius=10, **kwargs)


def form_row(*controls, columns=6):
    return ft.ResponsiveRow(controls=[ft.Column(col={"xs": 12, "md": columns},
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[control])
        for control in controls], spacing=16, run_spacing=16)


def polish(control):
    if isinstance(control, ft.Button) and not isinstance(control, ft.TextButton):
        control.height = 44
    if isinstance(control, (ft.TextField, ft.Dropdown)):
        control.filled = True
        control.fill_color = SURFACE
        control.border_color = BORDER
        control.focused_border_color = PRIMARY
        control.border_radius = 8
        control.text_size = 14
        control.label_style = ft.TextStyle(size=13, color=MUTED)
        control.content_padding = ft.Padding.symmetric(horizontal=14, vertical=15)
        if isinstance(control, ft.Dropdown):
            control.expanded_insets = 0
    if isinstance(control, ft.Text) and control.size and control.size >= 25:
        control.size = 30
        control.color = INK
        control.weight = ft.FontWeight.W_600
    for child in getattr(control, "controls", []) or []:
        polish(child)
    child = getattr(control, "content", None)
    if isinstance(child, ft.Control):
        polish(child)
    return control


def navigation_style(selected):
    return ft.ButtonStyle(bgcolor=TINT if selected else SURFACE,
        color=PRIMARY if selected else MUTED, icon_color=PRIMARY if selected else MUTED,
        shape=ft.RoundedRectangleBorder(radius=8), alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.Padding.symmetric(horizontal=16, vertical=16),
        text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400))
