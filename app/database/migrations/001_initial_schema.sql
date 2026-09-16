-- Amounts are integer Rupiah; quantity is integer thousandths; rates are basis points.
CREATE TABLE business_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    npwp TEXT NOT NULL DEFAULT '',
    bank_name TEXT NOT NULL DEFAULT '',
    bank_account_name TEXT NOT NULL DEFAULT '',
    bank_account_number TEXT NOT NULL DEFAULT '',
    responsible_person TEXT NOT NULL DEFAULT '',
    logo_path TEXT,
    qris_path TEXT,
    signature_path TEXT,
    stamp_path TEXT
) STRICT;

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'PERSONAL' CHECK (type IN ('PERSONAL','COMPANY')),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    company_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    npwp TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
) STRICT;
CREATE INDEX idx_customers_name ON customers(name COLLATE NOCASE);

CREATE TABLE catalog_items (
    id INTEGER PRIMARY KEY,
    sku TEXT UNIQUE CHECK (sku IS NULL OR length(trim(sku)) > 0),
    type TEXT NOT NULL CHECK (type IN ('PRODUCT','SERVICE')),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    default_price INTEGER NOT NULL DEFAULT 0 CHECK (default_price >= 0),
    unit TEXT NOT NULL DEFAULT 'pcs' CHECK (length(trim(unit)) > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
) STRICT;
CREATE INDEX idx_catalog_name ON catalog_items(name COLLATE NOCASE);

CREATE TABLE quotations (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    number TEXT UNIQUE CHECK (number IS NULL OR length(trim(number)) > 0),
    issue_date TEXT NOT NULL,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','SENT','ACCEPTED','REJECTED','EXPIRED','CONVERTED')),
    customer_snapshot TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(customer_snapshot)),
    business_snapshot TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(business_snapshot)),
    subtotal INTEGER NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    discount_type TEXT NOT NULL DEFAULT 'AMOUNT' CHECK (discount_type IN ('AMOUNT','PERCENT')),
    discount_value INTEGER NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
    discount_amount INTEGER NOT NULL DEFAULT 0 CHECK (discount_amount BETWEEN 0 AND subtotal),
    tax_rate_bps INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    tax_amount INTEGER NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    grand_total INTEGER NOT NULL DEFAULT 0 CHECK (grand_total = subtotal - discount_amount + tax_amount),
    notes TEXT NOT NULL DEFAULT '',
    terms TEXT NOT NULL DEFAULT '',
    CHECK (status = 'DRAFT' OR number IS NOT NULL),
    CHECK (discount_type != 'PERCENT' OR discount_value <= 10000),
    CHECK (valid_until IS NULL OR valid_until >= issue_date)
) STRICT;
CREATE INDEX idx_quotations_customer ON quotations(customer_id);
CREATE INDEX idx_quotations_status_date ON quotations(status, issue_date);

CREATE TABLE quotation_items (
    id INTEGER PRIMARY KEY,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
    catalog_item_id INTEGER REFERENCES catalog_items(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position > 0),
    name_snapshot TEXT NOT NULL CHECK (length(trim(name_snapshot)) > 0),
    description TEXT NOT NULL DEFAULT '',
    quantity_milli INTEGER NOT NULL CHECK (quantity_milli > 0),
    unit TEXT NOT NULL CHECK (length(trim(unit)) > 0),
    unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
    line_total INTEGER NOT NULL CHECK (line_total >= 0),
    UNIQUE (quotation_id, position)
) STRICT;
CREATE INDEX idx_quotation_items_catalog ON quotation_items(catalog_item_id);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    quotation_id INTEGER UNIQUE REFERENCES quotations(id) ON DELETE RESTRICT,
    number TEXT UNIQUE CHECK (number IS NULL OR length(trim(number)) > 0),
    issue_date TEXT NOT NULL,
    due_date TEXT,
    document_status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (document_status IN ('DRAFT','ISSUED','CANCELLED')),
    customer_snapshot TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(customer_snapshot)),
    business_snapshot TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(business_snapshot)),
    subtotal INTEGER NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    discount_type TEXT NOT NULL DEFAULT 'AMOUNT' CHECK (discount_type IN ('AMOUNT','PERCENT')),
    discount_value INTEGER NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
    discount_amount INTEGER NOT NULL DEFAULT 0 CHECK (discount_amount BETWEEN 0 AND subtotal),
    tax_rate_bps INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    tax_amount INTEGER NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    grand_total INTEGER NOT NULL DEFAULT 0 CHECK (grand_total = subtotal - discount_amount + tax_amount),
    notes TEXT NOT NULL DEFAULT '',
    terms TEXT NOT NULL DEFAULT '',
    CHECK (document_status != 'ISSUED' OR number IS NOT NULL),
    CHECK (discount_type != 'PERCENT' OR discount_value <= 10000),
    CHECK (due_date IS NULL OR due_date >= issue_date)
) STRICT;
CREATE INDEX idx_invoices_customer ON invoices(customer_id);
CREATE INDEX idx_invoices_status_due ON invoices(document_status, due_date);
CREATE INDEX idx_invoices_issue_date ON invoices(issue_date);

CREATE TABLE invoice_items (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
    catalog_item_id INTEGER REFERENCES catalog_items(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position > 0),
    name_snapshot TEXT NOT NULL CHECK (length(trim(name_snapshot)) > 0),
    description TEXT NOT NULL DEFAULT '',
    quantity_milli INTEGER NOT NULL CHECK (quantity_milli > 0),
    unit TEXT NOT NULL CHECK (length(trim(unit)) > 0),
    unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
    line_total INTEGER NOT NULL CHECK (line_total >= 0),
    UNIQUE (invoice_id, position)
) STRICT;
CREATE INDEX idx_invoice_items_catalog ON invoice_items(catalog_item_id);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
    payment_date TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    method TEXT NOT NULL CHECK (method IN ('CASH','TRANSFER','QRIS','OTHER')),
    reference TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID','VOID')),
    void_reason TEXT,
    CHECK (status != 'VOID' OR length(trim(coalesce(void_reason,''))) > 0)
) STRICT;
CREATE INDEX idx_payments_invoice_status ON payments(invoice_id, status);
CREATE INDEX idx_payments_date ON payments(payment_date);

CREATE TABLE receipts (
    id INTEGER PRIMARY KEY,
    payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id) ON DELETE RESTRICT,
    number TEXT NOT NULL UNIQUE CHECK (length(trim(number)) > 0),
    issued_date TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    document_snapshot TEXT NOT NULL CHECK (json_valid(document_snapshot)),
    status TEXT NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID','VOID')),
    void_reason TEXT,
    CHECK (status != 'VOID' OR length(trim(coalesce(void_reason,''))) > 0)
) STRICT;

CREATE TABLE document_sequences (
    document_type TEXT PRIMARY KEY CHECK (document_type IN ('QUOTATION','INVOICE','RECEIPT')),
    prefix TEXT NOT NULL CHECK (length(trim(prefix)) > 0),
    number_format TEXT NOT NULL DEFAULT '{prefix}-{year}-{seq:04d}',
    last_value INTEGER NOT NULL DEFAULT 0 CHECK (last_value >= 0)
) STRICT;
INSERT INTO document_sequences(document_type, prefix) VALUES
    ('QUOTATION','QUO'), ('INVOICE','INV'), ('RECEIPT','RCPT');

CREATE TABLE document_events (
    id INTEGER PRIMARY KEY,
    quotation_id INTEGER REFERENCES quotations(id) ON DELETE RESTRICT,
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE RESTRICT,
    receipt_id INTEGER REFERENCES receipts(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
    details TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details)),
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK ((quotation_id IS NOT NULL) + (invoice_id IS NOT NULL) + (receipt_id IS NOT NULL) = 1)
) STRICT;
CREATE INDEX idx_events_quotation ON document_events(quotation_id, id);
CREATE INDEX idx_events_invoice ON document_events(invoice_id, id);
CREATE INDEX idx_events_receipt ON document_events(receipt_id, id);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json))
) STRICT;

CREATE VIEW invoice_balances AS
SELECT invoice_id, grand_total, paid_amount, grand_total - paid_amount AS balance_due,
    CASE WHEN paid_amount >= grand_total THEN 'PAID'
         WHEN paid_amount = 0 THEN 'UNPAID' ELSE 'PARTIAL' END AS settlement_status,
    CASE WHEN document_status = 'ISSUED' AND due_date < date('now','localtime')
              AND paid_amount < grand_total THEN 1 ELSE 0 END AS is_overdue,
    CASE WHEN document_status = 'ISSUED' AND due_date < date('now','localtime')
              AND paid_amount < grand_total THEN 'OVERDUE'
         WHEN paid_amount >= grand_total THEN 'PAID'
         WHEN paid_amount = 0 THEN 'UNPAID' ELSE 'PARTIAL' END AS payment_status
FROM (
    SELECT i.id AS invoice_id, i.grand_total, i.document_status, i.due_date,
        coalesce(sum(CASE WHEN p.status = 'VALID' THEN p.amount ELSE 0 END),0) AS paid_amount
    FROM invoices i LEFT JOIN payments p ON p.invoice_id = i.id
    GROUP BY i.id
);

CREATE TRIGGER payment_insert_guard BEFORE INSERT ON payments
BEGIN
    SELECT CASE WHEN NEW.status != 'VALID' THEN RAISE(ABORT,'New payment must be valid') END;
    SELECT CASE WHEN coalesce((SELECT document_status FROM invoices WHERE id = NEW.invoice_id),'') != 'ISSUED'
        THEN RAISE(ABORT,'Payment requires an issued invoice') END;
    SELECT CASE WHEN NEW.amount > (SELECT balance_due FROM invoice_balances WHERE invoice_id = NEW.invoice_id)
        THEN RAISE(ABORT,'Payment exceeds remaining balance') END;
END;

CREATE TRIGGER payment_content_locked BEFORE UPDATE OF id, invoice_id, payment_date, amount, method, reference, notes ON payments
BEGIN SELECT RAISE(ABORT,'Void and replace payments instead of editing them'); END;

CREATE TRIGGER payment_void_guard BEFORE UPDATE OF status ON payments
WHEN NEW.status != OLD.status
BEGIN
    SELECT CASE WHEN OLD.status != 'VALID' OR NEW.status != 'VOID'
        THEN RAISE(ABORT,'Only valid to void is allowed') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM receipts WHERE payment_id = OLD.id AND status = 'VALID')
        THEN RAISE(ABORT,'Void the receipt before its payment') END;
END;

CREATE TRIGGER payment_no_delete BEFORE DELETE ON payments
BEGIN SELECT RAISE(ABORT,'Payments must be voided, not deleted'); END;

CREATE TRIGGER receipt_insert_guard BEFORE INSERT ON receipts
BEGIN
    SELECT CASE WHEN NEW.status != 'VALID' OR coalesce((SELECT status FROM payments WHERE id = NEW.payment_id),'') != 'VALID'
        THEN RAISE(ABORT,'Receipt requires a valid payment') END;
END;

CREATE TRIGGER receipt_content_locked BEFORE UPDATE OF id, payment_id, number, issued_date, description, document_snapshot ON receipts
BEGIN SELECT RAISE(ABORT,'Receipt content is immutable'); END;

CREATE TRIGGER receipt_void_guard BEFORE UPDATE OF status ON receipts
WHEN NEW.status != OLD.status AND (OLD.status != 'VALID' OR NEW.status != 'VOID')
BEGIN SELECT RAISE(ABORT,'Only valid to void is allowed'); END;

CREATE TRIGGER receipt_no_delete BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT,'Receipts must be voided, not deleted'); END;

CREATE TRIGGER invoice_content_locked BEFORE UPDATE OF id, customer_id, quotation_id, number,
    issue_date, due_date, customer_snapshot, business_snapshot, subtotal, discount_type,
    discount_value, discount_amount, tax_rate_bps, tax_amount, grand_total, notes, terms ON invoices
WHEN OLD.document_status != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Issued or cancelled invoice content is immutable'); END;

CREATE TRIGGER invoice_insert_draft BEFORE INSERT ON invoices
WHEN NEW.document_status != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'New invoice must start as draft'); END;

CREATE TRIGGER invoice_number_locked BEFORE UPDATE OF number ON invoices
WHEN OLD.number IS NOT NULL AND NEW.number IS NOT OLD.number
BEGIN SELECT RAISE(ABORT,'Assigned invoice number cannot change'); END;

CREATE TRIGGER invoice_status_guard BEFORE UPDATE OF document_status ON invoices
WHEN NEW.document_status != OLD.document_status
BEGIN
    SELECT CASE WHEN NOT ((OLD.document_status = 'DRAFT' AND NEW.document_status IN ('ISSUED','CANCELLED'))
        OR (OLD.document_status = 'ISSUED' AND NEW.document_status = 'CANCELLED'))
        THEN RAISE(ABORT,'Invalid invoice status transition') END;
    SELECT CASE WHEN NEW.document_status = 'CANCELLED' AND EXISTS
        (SELECT 1 FROM payments WHERE invoice_id = OLD.id AND status = 'VALID')
        THEN RAISE(ABORT,'Void payments before cancelling invoice') END;
END;

CREATE TRIGGER invoice_no_delete BEFORE DELETE ON invoices
WHEN OLD.document_status != 'DRAFT' OR OLD.number IS NOT NULL
BEGIN SELECT RAISE(ABORT,'Numbered or issued invoices cannot be deleted'); END;

CREATE TRIGGER invoice_item_insert_guard BEFORE INSERT ON invoice_items
WHEN (SELECT document_status FROM invoices WHERE id = NEW.invoice_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Invoice items can only change in draft'); END;
CREATE TRIGGER invoice_item_update_guard BEFORE UPDATE ON invoice_items
WHEN (SELECT document_status FROM invoices WHERE id = OLD.invoice_id) != 'DRAFT'
    OR (SELECT document_status FROM invoices WHERE id = NEW.invoice_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Invoice items can only change in draft'); END;
CREATE TRIGGER invoice_item_delete_guard BEFORE DELETE ON invoice_items
WHEN (SELECT document_status FROM invoices WHERE id = OLD.invoice_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Invoice items can only change in draft'); END;

CREATE TRIGGER quotation_content_locked BEFORE UPDATE OF id, customer_id, number, issue_date,
    valid_until, customer_snapshot, business_snapshot, subtotal, discount_type, discount_value,
    discount_amount, tax_rate_bps, tax_amount, grand_total, notes, terms ON quotations
WHEN OLD.status != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Sent quotation content is immutable'); END;

CREATE TRIGGER quotation_insert_draft BEFORE INSERT ON quotations
WHEN NEW.status != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'New quotation must start as draft'); END;

CREATE TRIGGER quotation_number_locked BEFORE UPDATE OF number ON quotations
WHEN OLD.number IS NOT NULL AND NEW.number IS NOT OLD.number
BEGIN SELECT RAISE(ABORT,'Assigned quotation number cannot change'); END;

CREATE TRIGGER quotation_status_guard BEFORE UPDATE OF status ON quotations
WHEN NEW.status != OLD.status AND NOT (
    (OLD.status = 'DRAFT' AND NEW.status = 'SENT') OR
    (OLD.status = 'SENT' AND NEW.status IN ('ACCEPTED','REJECTED','EXPIRED')) OR
    (OLD.status = 'ACCEPTED' AND NEW.status = 'CONVERTED'))
BEGIN SELECT RAISE(ABORT,'Invalid quotation status transition'); END;

CREATE TRIGGER quotation_no_delete BEFORE DELETE ON quotations
WHEN OLD.status != 'DRAFT' OR OLD.number IS NOT NULL
BEGIN SELECT RAISE(ABORT,'Numbered or sent quotations cannot be deleted'); END;

CREATE TRIGGER quotation_item_insert_guard BEFORE INSERT ON quotation_items
WHEN (SELECT status FROM quotations WHERE id = NEW.quotation_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Quotation items can only change in draft'); END;
CREATE TRIGGER quotation_item_update_guard BEFORE UPDATE ON quotation_items
WHEN (SELECT status FROM quotations WHERE id = OLD.quotation_id) != 'DRAFT'
    OR (SELECT status FROM quotations WHERE id = NEW.quotation_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Quotation items can only change in draft'); END;
CREATE TRIGGER quotation_item_delete_guard BEFORE DELETE ON quotation_items
WHEN (SELECT status FROM quotations WHERE id = OLD.quotation_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT,'Quotation items can only change in draft'); END;

CREATE TRIGGER sequence_no_decrease BEFORE UPDATE OF last_value ON document_sequences
WHEN NEW.last_value < OLD.last_value
BEGIN SELECT RAISE(ABORT,'Document sequence cannot decrease'); END;
CREATE TRIGGER sequence_no_delete BEFORE DELETE ON document_sequences
BEGIN SELECT RAISE(ABORT,'Document sequence cannot be deleted'); END;
CREATE TRIGGER sequence_type_locked BEFORE UPDATE OF document_type ON document_sequences
WHEN NEW.document_type != OLD.document_type
BEGIN SELECT RAISE(ABORT,'Sequence document type cannot change'); END;

CREATE TRIGGER event_no_update BEFORE UPDATE ON document_events
BEGIN SELECT RAISE(ABORT,'Document events are append-only'); END;
CREATE TRIGGER event_no_delete BEFORE DELETE ON document_events
BEGIN SELECT RAISE(ABORT,'Document events are append-only'); END;
