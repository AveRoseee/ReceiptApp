import asyncio
import logging
import flet as ft

from app.services import document_pdf_service as service

logger = logging.getLogger(__name__)


class DocumentPdfActions:
    def __init__(self, page, database_path, kind):
        self.page, self.database_path, self.kind = page, database_path, kind
        self.document = None
        self.busy = False
        self.picker = ft.FilePicker()
        page.services.append(self.picker)
        self.save = ft.Button(content="Simpan PDF", on_click=self.handle_save)
        self.open = ft.TextButton(content="Buka PDF", on_click=self.handle_open)
        self.control = ft.Row(visible=False, controls=[self.save, self.open])

    def show(self, document):
        enabled = self.kind == "RECEIPT" or document["document_status"] == "ISSUED"
        self.document = (document["id"], document["number"]) if enabled else None
        self.control.visible = enabled

    async def handle_save(self, event):
        await self.run(False)

    async def handle_open(self, event):
        await self.run(True)

    async def run(self, opening):
        if self.busy or self.document is None:
            return
        document_id, number = self.document
        self.busy = self.save.disabled = self.open.disabled = True
        self.save.content = "Memproses PDF..."
        self.page.update()
        try:
            if opening:
                await asyncio.to_thread(service.open_pdf, self.database_path, self.kind, document_id, number)
            else:
                data = await asyncio.to_thread(service.build_pdf, self.database_path, self.kind, document_id)
                selected = await self.picker.save_file(
                    dialog_title=f"Simpan {number}", file_name=service.safe_filename(number),
                    file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["pdf"], src_bytes=data)
                if not selected:
                    return
            self.page.show_dialog(ft.SnackBar(content=ft.Text(f"PDF {number} berhasil {'dibuka' if opening else 'disimpan'}.")))
        except (ValueError, LookupError) as error:
            self.page.show_dialog(ft.SnackBar(content=ft.Text(str(error))))
        except Exception:
            logger.exception("Gagal memproses PDF %s", number)
            self.page.show_dialog(ft.SnackBar(content=ft.Text("PDF belum dapat diproses. Periksa lokasi penyimpanan atau aplikasi pembaca PDF.")))
        finally:
            self.busy = self.save.disabled = self.open.disabled = False
            self.save.content = "Simpan PDF"
            self.page.update()
