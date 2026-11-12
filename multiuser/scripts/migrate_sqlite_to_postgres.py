"""Migrate data from an existing SQLite database to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_postgres.py --sqlite data/faresnipe.sqlite3 --postgres postgresql://user:pass@localhost:5432/faresnipe [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

try:
    import psycopg
except ImportError:
    print("psycopg is required. Install with: uv pip install 'psycopg[binary]'")
    sys.exit(1)


TABLES = [
    "fares",
    "alerts",
    "scan_runs",
    "scan_failures",
    "subscribers",
    "subscriptions",
    "notification_queue",
    "conversation_state",
]


def migrate(sqlite_path: str, postgres_url: str, dry_run: bool = False) -> None:
    """Copy all data from SQLite to PostgreSQL."""
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg.connect(postgres_url)

    try:
        for table in TABLES:
            # Check if table exists in SQLite
            row = sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not row:
                print(f"  Skipping {table}: not found in SQLite")
                continue

            # Check if table exists in PostgreSQL
            pg_exists = pg_conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
            ).fetchone()
            if not pg_exists:
                print(f"  Skipping {table}: not found in PostgreSQL (run migrations first)")
                continue

            # Get columns from SQLite
            columns = [
                str(col["name"])
                for col in sqlite_conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            if not columns:
                continue

            # Read all rows from SQLite
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"  {table}: 0 rows (empty)")
                continue

            col_list = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))

            if dry_run:
                print(f"  {table}: {len(rows)} rows (dry-run, not inserting)")
                continue

            # Insert into PostgreSQL
            with pg_conn.cursor() as cur:
                for row in rows:
                    values = [row[col] for col in columns]
                    cur.execute(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                        f"ON CONFLICT DO NOTHING",
                        values,
                    )
            pg_conn.commit()
            print(f"  {table}: {len(rows)} rows migrated")

    finally:
        sqlite_conn.close()
        pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="Path to SQLite database file")
    parser.add_argument("--postgres", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    args = parser.parse_args()

    print(f"Migrating from {args.sqlite} to PostgreSQL...")
    if args.dry_run:
        print("DRY RUN MODE - no data will be inserted")

    migrate(args.sqlite, args.postgres, dry_run=args.dry_run)
    print("Migration complete.")


if __name__ == "__main__":
    main()