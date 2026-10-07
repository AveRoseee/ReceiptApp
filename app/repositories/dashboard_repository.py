def read_metrics(connection, month_start, month_end, horizon):
    balances = [r[0] for r in connection.execute(
        "SELECT b.balance_due FROM invoices i JOIN invoice_balances b ON b.invoice_id=i.id WHERE i.document_status='ISSUED'"
    )]
    overdue = connection.execute(
        "SELECT count(*) FROM invoice_balances WHERE is_overdue=1"
    ).fetchone()[0]
    drafts = connection.execute("SELECT count(*) FROM invoices WHERE document_status='DRAFT'").fetchone()[0]
    payments = [r[0] for r in connection.execute(
        "SELECT amount FROM payments WHERE status='VALID' AND payment_date>=? AND payment_date<?",
        (month_start, month_end),
    )]
    due = [dict(row) for row in connection.execute(
        """SELECT i.id,i.number,i.due_date,b.balance_due,b.is_overdue,
            coalesce(json_extract(i.customer_snapshot,'$.data.name'),'') AS customer_name
            FROM invoices i JOIN invoice_balances b ON b.invoice_id=i.id
            WHERE i.document_status='ISSUED' AND b.balance_due>0 AND i.due_date<=?
            ORDER BY i.due_date,i.id LIMIT 20""", (horizon,),
    )]
    return balances, overdue, drafts, payments, due
