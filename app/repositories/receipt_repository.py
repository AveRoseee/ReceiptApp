import json


def get_receipt(connection, receipt_id):
    row = connection.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    return dict(row) if row else None


def get_by_payment(connection, payment_id):
    row = connection.execute("SELECT * FROM receipts WHERE payment_id=?", (payment_id,)).fetchone()
    return dict(row) if row else None


def insert_receipt(connection, payment_id, number, issued_date, description, snapshot):
    cursor = connection.execute(
        "INSERT INTO receipts(payment_id,number,issued_date,description,document_snapshot) VALUES (?,?,?,?,?)",
        (payment_id, number, issued_date, description,
         json.dumps(snapshot, ensure_ascii=False, sort_keys=True)),
    )
    return int(cursor.lastrowid)


def record_event(connection, receipt_id):
    connection.execute(
        "INSERT INTO document_events(receipt_id,event_type) VALUES (?, 'RECEIPT_ISSUED')",
        (receipt_id,),
    )
