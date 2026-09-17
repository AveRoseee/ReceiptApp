import asyncio
import logging
from pathlib import Path
import sqlite3

import flet as ft

from app.services.business_logo_service import (
    LogoValidationError,
    load_business_logo,
    save_business_logo
)
from app.services.business_profile_service import (
    ProfileValidationError
)


logger = logging.getLogger(__name__)


def build_business_logo(
    page: ft.Page,
    database_path: Path,
) -> ft.Column:
    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    preview = ft.Image(
        src="",
        width=180,
        height=120,
        fit=ft.BoxFit.CONTAIN,
        visible=False,
        error_content=ft.Text(
            "Pratinjau logo tidak dapat ditampilkan."
        ),
    )

    message = ft.Text(
        "Belum ada logo usaha.",
        color=ft.Colors.GREY_700
    )

    def display_logo(data: bytes) -> None:
        preview.src = data
        preview.visible = True

        message.value = "Logo usaha tersimpan."
        message.color = ft.Colors.GREEN_700

    async def handle_pick_logo(event) -> None:
        if pick_button.disabled:
            return

        pick_button.disabled = True
        page.update()

        try:
            selected_files = await file_picker.pick_files(
                dialog_title="Pilih logo usaha",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["png", "jpg", "jpeg"],
                allow_multiple=False,
            )

            if not selected_files:
                return

            selected_path = selected_files[0].path

            if not selected_path:
                raise LogoValidationError(
                    "Lokasi gambar tidak tersedia. "
                    "Gunakan aplikasi dekstop untuk memilih logo."
                )

            pick_button.content = "Menyimpan Logo..."
            page.update()


            data = await asyncio.to_thread(
                save_business_logo,
                database_path,
                Path(selected_path),
            )

            display_logo(data)

        except (
            LogoValidationError,
            ProfileValidationError,
        ) as error:
            message.value = str(error)
            message.color = ft.Colors.RED_700

        except Exception:
            logger.exception("Gagal memilih atau menyimpan logo")

            message.value = (
                "Logo belum berhasil diproses. "
                "Silahkan coba kembali."
            )
            message.color = ft.Colors.RED_700

        finally:
            pick_button.disabled = False
            pick_button.contennt = "Pilih & Simpan Logo"
            page.update()

    pick_button = ft.Button(
        content = "Pilih & Simpan Logo",
        on_click = handle_pick_logo,
    )

    try:
        saved_logo = load_business_logo(database_path)

        if saved_logo is not None:
            display_logo(saved_logo)

    except LogoValidationError as error:
        message.value = str(error)
        message.color = ft.Colors.RED_700

    except (sqlite3.Error, OSError):
        logger.exception("Gagal membaca logo yang tersimpan")

        message.value = "Logo yang tersimpan belum dapat dimuat."
        message.color = ft.Colors.RED_700

    return ft.Column(
        spacing=12,
        controls=[
            ft.Text(
                "Logo Usaha",
                size=18,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(
                "PNG atau JPG, maksimal 5 MB. "
                "Simpan Profil usaha sebelum menambahkan logo.",
                color=ft.Colors.GREY_700,
            ),
            preview,
            message,
            pick_button
        ],
    )