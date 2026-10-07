from pathlib import Path


def database_path(connection):
    rows = connection.execute("PRAGMA database_list").fetchall()
    return next((Path(row[2]) for row in rows if row[1] == "main" and row[2]), None)
