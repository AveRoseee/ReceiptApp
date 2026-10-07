from contextlib import closing
from io import BytesIO
from pathlib import Path
import asyncio

from pypdf import PdfReader
import pytest
import flet as ft

from app.database import connect
from app.services import document_pdf_service as pdf, receipt_service
from app.components.document_pdf_actions import DocumentPdfActions
from test_core_invoice import business, record
from test_view_interactions import FakePage


def test_invoice_and_receipt_pdf_snapshot_and_output(business):
    path, invoice = business
    with closing(connect(path)) as db:
        payment = record(db, invoice)
        receipt = receipt_service.issue_receipt(db, payment["id"])
        output = path.parent / "receipt.pdf"
        pdf.generate_receipt_pdf(db, receipt["id"], output)
        text = "\n".join(p.extract_text() for p in PdfReader(output).pages)
        assert "Tiga ratus ribu rupiah" in text
        assert "Usaha Awal" in text
        assert invoice["number"] in text
        assert receipt["number"] in text
        pdf.generate_invoice_pdf(db, invoice["id"], path.parent / "invoice.pdf")
        assert (path.parent / "invoice.pdf").read_bytes().startswith(b"%PDF-")


def test_failed_write_preserves_previous_file(business, monkeypatch):
    path, invoice = business
    output = path.parent / "invoice.pdf"
    output.write_bytes(b"previous")
    def fail(*args):
        raise PermissionError("replace failed")
    monkeypatch.setattr(pdf.os, "replace", fail)
    with closing(connect(path)) as db:
        with pytest.raises(pdf.DocumentPdfError):
            pdf.generate_invoice_pdf(db, invoice["id"], output)
    assert output.read_bytes() == b"previous"
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_output_and_nested_read(business):
    path, invoice = business
    with closing(connect(path)) as db:
        db.execute("BEGIN")
        assert pdf.invoice_bytes(db, invoice["id"]).startswith(b"%PDF-")
        assert db.in_transaction
        with pytest.raises(pdf.DocumentPdfError):
            pdf.generate_invoice_pdf(db, invoice["id"], path)
        with pytest.raises(pdf.DocumentPdfError):
            pdf.generate_invoice_pdf(db, invoice["id"], path.parent / "missing/file.pdf")


def test_dialog_cancel_retry_and_duplicate_click(business, monkeypatch):
    path, invoice = business
    page = FakePage()
    actions = DocumentPdfActions(page, path, "INVOICE")
    actions.show(invoice)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def save(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            await release.wait()
            return None
        monkeypatch.setattr(ft.FilePicker, "save_file", save)
        task = asyncio.create_task(actions.handle_save(None))
        await asyncio.wait_for(entered.wait(), 10)
        await actions.handle_save(None)
        release.set()
        await task
        assert len(calls) == 1 and calls[0]["src_bytes"].startswith(b"%PDF-")
    asyncio.run(scenario())
    assert not page.dialogs and not actions.busy
    assert not actions.save.disabled


def test_open_pdf_uses_native_viewer(business, monkeypatch):
    path, invoice = business
    opened = []
    monkeypatch.setattr(pdf.os, "startfile", opened.append, raising=False)
    result = pdf.open_pdf(path, "INVOICE", invoice["id"], invoice["number"])
    assert opened == [str(result)]
    assert result.read_bytes().startswith(b"%PDF-")
