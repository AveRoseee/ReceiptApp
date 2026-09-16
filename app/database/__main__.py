"""Run with python -m app.database --path <database file>."""

import argparse

from .database import initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize DokumenUsaha database")
    parser.add_argument("--path", help="Database file; defaults to the user data directory")
    arguments = parser.parse_args()
    print(f"Database ready: {initialize_database(arguments.path)}")


if __name__ == "__main__":
    main()
