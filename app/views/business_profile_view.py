from contextlib import closing
import logging
from pathlib import Path
import sqlite3
from typing import Any

import flet as ft

from app.database import connect
from app.services.business_profile_service import (
    ProfileValidationError,
    get_business_profile,
    save_business_profile,
)
from app.components.business_logo import build_business_logo

logger = logging.getLogger(__name__)


def build_business_profile_view(
    page: ft.Page,
    database_path: Path,
) -> ft.Column:
    fields: dict[str, ft.TextField] = {
        "name": ft.TextField(
            label="Nama Usaha *",
            hint_text="Contoh: Bandung Creative Studio",
            col=12,
        ),
        "address": ft.TextField(
            label="Alamat",
            multiline=True,
            min_lines=2,
            max_lines=4,
            col=12,
        ),
        "responsible_person": ft.TextField(
            label="Nama penanggung jawab",
            col={"xs": 12, "md": 6},
        ),
        "npwp": ft.TextField(
            label="NPWP",
            col={"xs": 12, "md": 6},
        ),
        "phone": ft.TextField(
            label="Nomor Telepon",
            col={"xs": 12, "md": 6}
        ),
        "whatsapp": ft.TextField(
            label="Nomor Whatsapp",
            col={"xs": 12, "md": 6}
        ),
        "email": ft.TextField(
            label="Alamat Email",
            col={"xs": 12, "md": 6}
        ),
        "website": ft.TextField(
            label="Website",
            col={"xs": 12, "md": 6},
        ),
        "bank_name": ft.TextField(
            label="Nama Bank",
            hint_text="Contoh: BCA, SeaBank, MANDIRI, Allo Bank",
            col={"xs": 12, "md": 6}
        ),
        "bank_account_number": ft.TextField(
            label="Nomor Rekening",
            col={"xs": 12, "md": 6}
        ),
        "bank_account_name": ft.TextField(
            label="Nama Pemilik Rekening",
            col={"xs": 12, "md": 6}
        ),
    }

    status_text = ft.Text(
        value="Isi profil usaha Anda, lalu klik Simpan.",
        color=ft.Colors.GREY_700,
    )

    def populate_fields(profile: dict[str, Any]) -> None:
        for field_name, control in fields.items():
            control.value = profile.get(field_name) or ""

    def show_message(
        message: str,
        *,
        is_error: bool = False,
    ) -> None:
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(
                    message,
                    color=ft.Colors.WHITE,
                ),
                bgcolor=(
                    ft.Colors.RED_700
                    if is_error
                    else ft.Colors.GREEN_700
                )
            )
        )

    def handle_save(event) -> None:
        if save_button.disabled:
            return

        fields["name"].error = None
        save_button.disabled = True
        save_button.content = "Menyimpan..."
        page.update()

        try:
            data = {
                field_name: control.value or ""
                for field_name, control in fields.items()
            }

            with closing(connect(database_path)) as connection:
                profile = save_business_profile(
                    connection,
                    data,
                )

            populate_fields(profile)

            status_text.value = "Perubahan terakhir berhasil disimpan."
            status_text.color = ft.Colors.GREEN_700

            show_message("Profil usaha berhasil disimpan.")

        except ProfileValidationError as error:
            if not (fields["name"].value or "").strip():
                fields["name"].error = str(error)

            status_text.value = "Periksa kembali isian profil usaha."
            status_text.color = ft.Colors.RED_700

            show_message(str(error), is_error=True)

        except (sqlite3.Error, OSError):
            logger.exception("Gagal menyimpan profil usaha")

            status_text.value = "Perubahan belum tersimpan."
            status_text.color = ft.Colors.RED_700

            show_message(
                "Profil belum berhasil disimpan. Silakan coba kembali.",
                is_error=True,
            )

        finally:
            save_button.disabled = False
            save_button.content = "Simpan Profil"
            page.update()

    save_button = ft.Button(
        content="Simpan Profil",
        icon=ft.Icons.SAVE_OUTLINED,
        on_click=handle_save,
    )

    with closing(connect(database_path)) as connection:
        current_profile = get_business_profile(connection)

    if current_profile is not None:
        populate_fields(current_profile)
        status_text.value = "Profil usaha tersimpan telah dimuat."

    return ft.Column(
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        spacing=20,
        controls=[
            ft.Text(
                "Profil Usaha",
                size=28,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(
                "Informasi ini akan digunakan pada penawaran, "
                "invoice, dan kwitansi Anda.",
                color=ft.Colors.GREY_700,
            ),
            build_business_logo(
                page,
                database_path,
            ),
            ft.Divider(),
            ft.Text(
                "Identitas Usaha",
                size=18,
                weight=ft.FontWeight.BOLD,
            ),
            ft.ResponsiveRow(
                spacing=16,
                run_spacing=16,
                controls=[
                    fields["name"],
                    fields["address"],
                    fields["responsible_person"],
                    fields["npwp"],
                ],
            ),
            ft.Divider(),
            ft.Text(
                "Kontak",
                size=18,
                weight=ft.FontWeight.BOLD,
            ),
            ft.ResponsiveRow(
                spacing=16,
                run_spacing=16,
                controls=[
                    fields["phone"],
                    fields["whatsapp"],
                    fields["email"],
                    fields["website"],
                ],
            ),
            ft.Divider(),
            ft.Text(
                "Rekening Pembayaran",
                size=18,
                weight=ft.FontWeight.BOLD,
            ),
            ft.ResponsiveRow(
                spacing=16,
                run_spacing=16,
                controls=[
                    fields["bank_name"],
                    fields["bank_account_number"],
                    fields["bank_account_name"],
                ],
            ),
            status_text,
            ft.Row(
                controls=[save_button],
                alignment=ft.MainAxisAlignment.END,
            ),
        ],
    )