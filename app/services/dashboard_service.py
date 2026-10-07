from datetime import date, timedelta
from app.repositories.dashboard_repository import read_metrics


def get_dashboard(connection):
    today = date.today()
    start = today.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    own = not connection.in_transaction
    if own:
        connection.execute("BEGIN")
    try:
        balances, overdue, drafts, payments, due = read_metrics(
            connection, start.isoformat(), next_month.isoformat(), (today+timedelta(days=7)).isoformat())
        return {"outstanding": sum(balances), "overdue_count": overdue, "draft_count": drafts,
                "month_received": sum(payments), "due_invoices": due}
    finally:
        if own:
            connection.rollback()
