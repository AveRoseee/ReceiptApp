import sqlite3

import pytest

from app.database import connect, initialize_database, transaction
from app.database.database import MIGRATIONS_DIR, migrate


@pytest.fixture
def db(tmp_path):
    path = initialize_database(tmp_path / 'business.db')
    connection = connect(path)
    connection.execute("INSERT INTO customers(id,name) VALUES (1,'Pelanggan Contoh')")
    yield connection
    connection.close()


def invoice(db, amount=100000, number='INV-2026-0001', due='2099-12-31'):
    cursor = db.execute("""INSERT INTO invoices(customer_id,number,issue_date,due_date,
        subtotal,grand_total) VALUES (1,?,'2026-09-16',?,?,?)""", (number, due, amount, amount))
    invoice_id = cursor.lastrowid
    db.execute("""INSERT INTO invoice_items(invoice_id,position,name_snapshot,
        quantity_milli,unit,unit_price,line_total) VALUES (?,1,'Jasa',1000,'project',?,?)""",
        (invoice_id, amount, amount))
    db.execute("UPDATE invoices SET document_status='ISSUED' WHERE id=?", (invoice_id,))
    return invoice_id


def pay(db, invoice_id, amount):
    return db.execute("""INSERT INTO payments(invoice_id,payment_date,amount,method)
        VALUES (?,'2026-09-16',?,'TRANSFER')""", (invoice_id, amount)).lastrowid


def balance(db, invoice_id):
    return db.execute("SELECT * FROM invoice_balances WHERE invoice_id=?", (invoice_id,)).fetchone()


def test_initialization_is_repeatable(tmp_path):
    path = initialize_database(tmp_path / 'nested' / 'business.db')
    initialize_database(path)
    connection = connect(path)
    try:
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 13
        assert connection.execute('SELECT count(*) FROM document_sequences').fetchone()[0] == 3
    finally:
        connection.close()


def test_failed_migration_rolls_back_entire_batch(tmp_path):
    directory = tmp_path / 'migrations'
    directory.mkdir()
    (directory / '001_ok.sql').write_text('CREATE TABLE example (id INTEGER);', encoding='utf-8')
    (directory / '002_bad.sql').write_text('INSERT INTO missing VALUES (1);', encoding='utf-8')
    connection = connect(':memory:')
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(connection, directory)
        assert not connection.in_transaction
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        connection.close()


def test_changed_migration_is_rejected(db, tmp_path):
    original = (MIGRATIONS_DIR / '001_initial_schema.sql').read_bytes()
    (tmp_path / '001_initial_schema.sql').write_bytes(original + b'\n')
    with pytest.raises(ValueError, match='has changed'):
        migrate(db, tmp_path)


def test_newer_database_is_rejected(db):
    db.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (2,'002_future.sql','future')")
    with pytest.raises(ValueError, match='newer'):
        migrate(db)


def test_transaction_rolls_back_business_operation(db):
    with pytest.raises(RuntimeError):
        with transaction(db):
            db.execute("INSERT INTO customers(name) VALUES ('Must roll back')")
            raise RuntimeError('simulated failure')
    assert db.execute('SELECT count(*) FROM customers').fetchone()[0] == 1


def test_nested_transaction_does_not_commit_outer_work(db):
    with pytest.raises(RuntimeError, match='Nested'):
        with transaction(db):
            db.execute("INSERT INTO customers(name) VALUES ('Nested')")
            with transaction(db):
                pass
    assert db.execute('SELECT count(*) FROM customers').fetchone()[0] == 1


def test_partial_then_paid_and_overpayment_rejected(db):
    identifier = invoice(db)
    assert balance(db, identifier)['payment_status'] == 'UNPAID'
    pay(db, identifier, 30000)
    assert balance(db, identifier)['balance_due'] == 70000
    assert balance(db, identifier)['payment_status'] == 'PARTIAL'
    with pytest.raises(sqlite3.IntegrityError, match='exceeds'):
        pay(db, identifier, 70001)
    pay(db, identifier, 70000)
    assert balance(db, identifier)['payment_status'] == 'PAID'
    assert balance(db, identifier)['balance_due'] == 0


def test_overdue_keeps_partial_information(db):
    # Bind the due date to an unambiguously historical date without depending on today's date.
    other = db.execute("""INSERT INTO invoices(customer_id,number,issue_date,due_date,subtotal,grand_total)
        VALUES (1,'INV-PAST','2000-01-01','2000-01-02',100000,100000)""").lastrowid
    db.execute("UPDATE invoices SET document_status='ISSUED' WHERE id=?", (other,))
    pay(db, other, 25000)
    row = balance(db, other)
    assert (row['payment_status'], row['settlement_status'], row['is_overdue']) == ('OVERDUE','PARTIAL',1)


@pytest.mark.parametrize('amount', [0, -1, 1.5])
def test_invalid_payment_amount_rejected(db, amount):
    with pytest.raises(sqlite3.IntegrityError):
        pay(db, invoice(db), amount)


def test_payment_requires_issued_invoice(db):
    identifier = db.execute("INSERT INTO invoices(customer_id,issue_date) VALUES (1,'2026-09-16')").lastrowid
    with pytest.raises(sqlite3.IntegrityError, match='issued'):
        pay(db, identifier, 1)


def test_receipt_unique_and_void_workflow(db):
    identifier = invoice(db)
    payment_id = pay(db, identifier, 30000)
    db.execute("""INSERT INTO receipts(payment_id,number,issued_date,document_snapshot)
        VALUES (?,'RCPT-2026-0001','2026-09-16','{}')""", (payment_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO receipts(payment_id,number,issued_date,document_snapshot)
            VALUES (?,'RCPT-2026-0002','2026-09-16','{}')""", (payment_id,))
    with pytest.raises(sqlite3.IntegrityError, match='receipt'):
        db.execute("UPDATE payments SET status='VOID',void_reason='Salah input' WHERE id=?", (payment_id,))
    with transaction(db):
        db.execute("UPDATE receipts SET status='VOID',void_reason='Salah input' WHERE payment_id=?", (payment_id,))
        db.execute("UPDATE payments SET status='VOID',void_reason='Salah input' WHERE id=?", (payment_id,))
    assert balance(db, identifier)['balance_due'] == 100000
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE payments SET status='VALID' WHERE id=?", (payment_id,))


def test_issued_invoice_and_items_are_immutable(db):
    identifier = invoice(db)
    for sql in [
        "UPDATE invoices SET subtotal=90000,grand_total=90000 WHERE id=?",
        "UPDATE invoices SET document_status='DRAFT' WHERE id=?",
        "DELETE FROM invoices WHERE id=?",
        "UPDATE invoice_items SET unit_price=2 WHERE invoice_id=?",
        "DELETE FROM invoice_items WHERE invoice_id=?",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, (identifier,))
    payment_id = pay(db, identifier, 100)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE payments SET amount=200 WHERE id=?", (payment_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE invoices SET document_status='CANCELLED' WHERE id=?", (identifier,))


def test_foreign_keys_and_document_number_uniqueness(db):
    identifier = invoice(db)
    with pytest.raises(sqlite3.IntegrityError):
        invoice(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('DELETE FROM customers WHERE id=1')
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO invoices(customer_id,issue_date) VALUES (999,'2026-09-16')")
    db.execute('UPDATE customers SET is_active=0 WHERE id=1')
    assert balance(db, identifier)['grand_total'] == 100000


def test_quotation_can_convert_only_once_and_locks_items(db):
    quotation_id = db.execute("INSERT INTO quotations(customer_id,number,issue_date) VALUES (1,'QUO-1','2026-09-16')").lastrowid
    db.execute("""INSERT INTO quotation_items(quotation_id,position,name_snapshot,quantity_milli,unit,unit_price,line_total)
        VALUES (?,1,'Jasa',1500,'jam',100000,150000)""", (quotation_id,))
    for status in ['SENT','ACCEPTED','CONVERTED']:
        db.execute('UPDATE quotations SET status=? WHERE id=?', (status, quotation_id))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('UPDATE quotation_items SET line_total=0 WHERE quotation_id=?', (quotation_id,))
    db.execute("INSERT INTO invoices(customer_id,quotation_id,issue_date) VALUES (1,?,'2026-09-16')", (quotation_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO invoices(customer_id,quotation_id,issue_date) VALUES (1,?,'2026-09-16')", (quotation_id,))


def test_sequence_and_events_cannot_be_erased(db):
    db.execute("UPDATE document_sequences SET last_value=3 WHERE document_type='INVOICE'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE document_sequences SET last_value=2 WHERE document_type='INVOICE'")
    identifier = invoice(db)
    db.execute("INSERT INTO document_events(invoice_id,event_type) VALUES (?,'ISSUED')", (identifier,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('DELETE FROM document_events')


def test_single_business_and_valid_json(db):
    db.execute("INSERT INTO business_profile(id,name) VALUES (1,'Usaha Contoh')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO business_profile(id,name) VALUES (2,'Usaha Lain')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO app_settings(key,value_json) VALUES ('bad','not json')")


def test_number_cannot_be_recycled_from_numbered_draft(db):
    identifier = db.execute("""INSERT INTO invoices(customer_id,number,issue_date)
        VALUES (1,'INV-RESERVED','2026-09-16')""").lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('UPDATE invoices SET number=NULL WHERE id=?', (identifier,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('DELETE FROM invoices WHERE id=?', (identifier,))


def test_documents_cannot_skip_draft_on_insert(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO invoices(customer_id,number,issue_date,document_status)
            VALUES (1,'INV-SKIP','2026-09-16','ISSUED')""")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO quotations(customer_id,number,issue_date,status)
            VALUES (1,'QUO-SKIP','2026-09-16','SENT')""")
