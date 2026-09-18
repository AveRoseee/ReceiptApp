from pathlib import Path
import asyncio
import logging
import sqlite3

import flet as ft

from app.services.business_image_service import (
    BusinessImageValidationError,
    load_business_image,
    save_business_image,
)
from app.services.business_profile_service import (
    ProfileValidationError,
)


logger = logging.getLogger(__name__)

IMAGE_LABELS = {
    "qris": "QRIS",
    "signature": "Tanda Tangan",
    "stamp": "Stempel",
}


def build_business_image(
    page: ft.Page,
    database_path: Path,
    image_type: str,
) -> ft.Column:
    if image_type not in IMAGE_LABELS:
        raise ValueError(
            f"Jenis komponen gambar tidak dikenal: {image_type}"
        )

    title = IMAGE_LABELS[image_type]

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    preview = ft.Image(
        src="",
        width=220,
        height=220 if image_type == "qris" else 140,
        fit=ft.BoxFit.CONTAIN,
        visible=False,
        error_content=ft.Text(
            "Pratinjau gambar tidak dapat ditampilkan."
        ),
    )

    message = ft.Text(
        f"Belum ada gambar {title.lower()}.",
        color=ft.Colors.GREY_700,
    )

    def display_image(data: bytes) -> None:
        preview.src = data
        preview.visible = True

        message.value = f"{title} tersimpan."
        message.color = ft.Colors.GREEN_700

    async def handle_pick_image(event) -> None:
        if pick_button.disabled:
            return

        pick_button.disabled = True
        page.update()

        try:
            selected_files = await file_picker.pick_files(
                dialog_title=f"Pilih gambar {title.lower()}",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["png", "jpg", "jpeg"],
                allow_multiple=False,
            )

            if not selected_files:
                return

            selected_path = selected_files[0].path

            if not selected_path:
                raise BusinessImageValidationError(
                    "Lokasi gambar tidak tersedia. "
                    "Gunakan aplikasi desktop."
                )

            pick_button.content = "Menyimpan..."
            page.update()

            data = await asyncio.to_thread(
                save_business_image,
                database_path,
                Path(selected_path),
                image_type,
            )

            display_image(data)

        except (
            BusinessImageValidationError,
            ProfileValidationError,
        ) as error:
            message.value = str(error)
            message.color = ft.Colors.RED_700

        except Exception:
            logger.exception(
                "Gagal memilih atau menyimpan gambar %s",
                image_type,
            )

            message.value = (
                "Gambar belum berhasil diproses. "
                "Silakan coba kembali."
            )
            message.color = ft.Colors.RED_700

        finally:
            pick_button.disabled = False
            pick_button.content = f"Pilih & Simpan {title}"
            page.update()

    pick_button = ft.Button(
        content=f"Pilih & Simpan {title}",
        on_click=handle_pick_image,
    )

    try:
        saved_image = load_business_image(
            database_path,
            image_type,
        )

        if saved_image is not None:
            display_image(saved_image)

    except BusinessImageValidationError as error:
        message.value = str(error)
        message.color = ft.Colors.RED_700

    except (sqlite3.Error, OSError):
        logger.exception(
            "Gagal membaca gambar %s",
            image_type,
        )

        message.value = "Gambar tersimpan belum dapat dimuat."
        message.color = ft.Colors.RED_700

    return ft.Column(
        spacing=12,
        controls=[
            ft.Text(
                title,
                size=18,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(
                "PNG atau JPG, maksimal 5 MB.",
                color=ft.Colors.GREY_700,
            ),
            preview,
            message,
            pick_button,
        ],
    )