"""Quotation-specific configuration for the shared document form."""
from app.services import quotation_service as service
from app.views.document_editor import (
    DocumentEditor, MAX_INTEGER, input_number, load_all, parse_number, rupiah,
)


class QuotationEditor(DocumentEditor):
    def __init__(self, page, database_path, on_saved, on_cancel):
        super().__init__(
            page, database_path, on_saved, on_cancel,
            service=service,
            get_document=service.get_quotation,
            document_label="Penawaran",
            deadline_field="valid_until",
            deadline_label="Berlaku sampai (opsional)",
            status_field="status",
        )
        self.valid_until = self.deadline

    @property
    def quotation_id(self):
        return self.document_id

    @quotation_id.setter
    def quotation_id(self, value):
        self.document_id = value
