"""Invoice-specific configuration for the shared document form."""
from app.services import invoice_service as service
from app.views.document_editor import DocumentEditor


class InvoiceEditor(DocumentEditor):
    def __init__(self, page, database_path, on_saved, on_cancel):
        super().__init__(
            page, database_path, on_saved, on_cancel,
            service=service,
            get_document=service.get_invoice,
            document_label="Invoice",
            deadline_field="due_date",
            deadline_label="Jatuh tempo (opsional, YYYY-MM-DD)",
            status_field="document_status",
        )
