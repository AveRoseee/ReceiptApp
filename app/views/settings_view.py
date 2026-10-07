import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
from uuid import uuid4

import flet as ft
from app.components.design import panel, MUTED

from app.services import backup_service as service

logger = logging.getLogger(__name__)


def build_settings_view(page, database_path, on_restored):
    database_path = Path(database_path)
    picker = ft.FilePicker()
    page.services.append(picker)
    message = ft.Text(selectable=True)
    busy = False

    def lock(value):
        nonlocal busy
        busy = value
        backup.disabled = restore.disabled = location.disabled = value
        page.update()

    async def backup_now(event):
        if busy:
            return
        lock(True)
        try:
            directory = await picker.get_directory_path(dialog_title="Pilih folder cadangan")
            if not directory:
                return
            name = "DokumenUsaha-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8] + ".zip"
            output = Path(directory) / name
            message.value = "Membuat cadangan..."
            page.update()
            await asyncio.to_thread(service.create_backup, database_path, output)
            message.value = f"Cadangan tersimpan: {output}"
        except (ValueError, OSError) as error:
            message.value = str(error)
        except Exception:
            logger.exception("Backup gagal")
            message.value = "Cadangan belum dapat dibuat. Coba kembali setelah operasi lain selesai."
        finally:
            lock(False)

    async def choose_restore(event):
        if busy:
            return
        lock(True)
        try:
            files = await picker.pick_files(dialog_title="Pilih cadangan DokumenUsaha", allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["zip"])
            if not files or not files[0].path:
                return
            selected = files[0].path
            handled = False

            def cancel(event):
                nonlocal handled
                handled = True
                page.pop_dialog()

            async def confirm(event):
                nonlocal handled
                if handled or busy:
                    return
                handled = True
                page.pop_dialog()
                lock(True)
                root = page.controls[0] if page.controls else None
                if root:
                    root.disabled = True
                message.value = "Memvalidasi dan memulihkan cadangan..."
                page.update()
                success = False
                try:
                    safety = await asyncio.to_thread(service.restore_backup, database_path, selected)
                    success = True
                except Exception as error:
                    logger.exception("Restore gagal")
                    message.value = f"Pemulihan gagal: {error}"
                finally:
                    if root:
                        root.disabled = False
                    lock(False)
                if success:
                    on_restored()
                    page.show_dialog(ft.SnackBar(content=ft.Text(f"Data dipulihkan. Cadangan sebelum pemulihan: {safety}")))

            page.show_dialog(ft.AlertDialog(
                modal=True, title=ft.Text("Ganti seluruh data lokal?"),
                content=ft.Text("Pemulihan mengganti SEMUA data dan gambar usaha pada perangkat ini. "
                                "Perubahan yang belum disimpan akan hilang. Data tidak digabungkan. "
                                "Cadangan otomatis dibuat sebelum penggantian."),
                actions=[ft.TextButton(content="Batal", on_click=cancel),
                         ft.Button(content="Ya, Pulihkan Seluruh Data", on_click=confirm)],
            ))
        except Exception as error:
            logger.exception("Gagal memilih cadangan")
            message.value = f"Cadangan belum dapat dipilih: {error}"
        finally:
            lock(False)

    def open_location(event):
        try:
            os.startfile(str(database_path.resolve().parent))
        except OSError:
            message.value = "Lokasi data belum dapat dibuka."
            page.update()

    backup = ft.Button(content="Buat Cadangan Sekarang", on_click=backup_now)
    restore = ft.Button(content="Pulihkan dari Cadangan", on_click=choose_restore)
    location = ft.TextButton(content="Buka Lokasi Data", on_click=open_location)
    backup.icon = ft.Icons.SAVE_ALT_OUTLINED
    restore.icon = ft.Icons.RESTORE_OUTLINED
    return ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=24, controls=[
        ft.Text("Pengaturan dan Cadangan", size=25, weight=ft.FontWeight.BOLD),
        ft.Text("Simpan cadangan ZIP ke perangkat penyimpanan lain secara berkala."),
        panel(ft.Column(spacing=16, controls=[
            ft.Text("Cadangkan data usaha", size=20, weight=ft.FontWeight.W_600),
            ft.Text("Simpan transaksi dan gambar dalam satu file ZIP.", color=MUTED), backup])),
        panel(ft.Column(spacing=16, controls=[
            ft.Text("Pulihkan data", size=20, weight=ft.FontWeight.W_600),
            ft.Text("Cadangan akan mengganti seluruh data di perangkat ini.", color=MUTED), restore])),
        location, message,
    ])
