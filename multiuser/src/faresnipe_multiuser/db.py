"""Database abstraction layer supporting both SQLite and PostgreSQL.

This module provides a thin wrapper around SQLite and PostgreSQL so the rest
of the codebase can use a single API regardless of backend. The key design
decisions:

- Placeholder conversion (``?`` -> ``%s``) is done with a real SQL string
  parser that tracks string literals, so ``?`` inside quoted strings (e.g.
  booking URLs) is never corrupted.
- Booleans are handled via ``bool_true``/``bool_false`` helpers so queries
  never hardcode ``= 1`` or ``= TRUE``.
- PostgreSQL uses a connection pool (``psycopg_pool``) to avoid exhausting
  ``max_connections`` under concurrent processes (bot, scanner, delivery).
- Prices are stored as TEXT (preserving ``Decimal``) and cast to NUMERIC
  when needed for ordering/aggregation — never REAL (float4) which loses
  precision.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # pragma: no cover - optional dependency
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _HAS_PSYCOPG = True
except ImportError:  # pragma: no cover
    _HAS_PSYCOPG = False


class DatabaseError(Exception):
    """Raised for database-level errors."""


def _convert_placeholders(query: str) -> str:
    """Convert ``?`` to ``%s`` only outside SQL string literals.

    This is a real parser, not a naive ``str.replace``. It tracks single-quote
    string literals (including escaped ``''``) so a ``?`` inside a quoted
    string (e.g. a booking URL like ``https://...?dep=...``) is left alone.
    """
    result: list[str] = []
    in_string = False
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if ch == "'":
            if in_string and i + 1 < n and query[i + 1] == "'":
                # Escaped quote inside a string literal: '' -> keep both.
                result.append("''")
                i += 2
                continue
            in_string = not in_string
            result.append(ch)
        elif ch == "?" and not in_string:
            result.append("%s")
        else:
            result.append(ch)
        i += 1
    return "".join(result)


class _SQLiteConnection:
    """Wrapper around a sqlite3 connection exposing a psycopg-like API."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        return self._conn.execute(query, params)

    def executemany(self, query: str, params: list[tuple[Any, ...]]) -> Any:
        return self._conn.executemany(query, params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    @property
    def lastrowid(self) -> int:
        return int(self._conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def __enter__(self) -> "_SQLiteConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        # Do NOT close here — Database.connect() handles cleanup in its finally.
        if exc[0] is None:
            self.commit()
        else:
            self.rollback()


class _PostgresConnection:
    """Wrapper around a psycopg connection exposing a unified API."""

    def __init__(self, conn: Any, pool: ConnectionPool) -> None:
        self._conn = conn
        self._pool = pool

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        return self._conn.execute(_convert_placeholders(query), params)

    def executemany(self, query: str, params: list[tuple[Any, ...]]) -> Any:
        return self._conn.executemany(_convert_placeholders(query), params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._pool.putconn(self._conn)

    @property
    def lastrowid(self) -> int:
        # psycopg3: cursor.lastrowid is None; use RETURNING instead.
        raise NotImplementedError("Use RETURNING id for PostgreSQL")

    def __enter__(self) -> "_PostgresConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        # Do NOT close here — Database.connect() handles pool return in its finally.
        if exc[0] is None:
            self.commit()
        else:
            self.rollback()


class Database:
    """Unified database access for SQLite and PostgreSQL.

    Usage::

        db = Database("sqlite:///data/faresnipe.sqlite3")
        with db.connect() as conn:
            conn.execute("INSERT INTO ... VALUES (?, ?)", (1, "x"))
            conn.commit()
    """

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            if not _HAS_PSYCOPG:
                raise DatabaseError(
                    "PostgreSQL support requires psycopg. Install with: "
                    "uv pip install 'psycopg[binary,pool]'"
                )
            self.backend = "postgresql"
            self._pool = ConnectionPool(url, min_size=2, max_size=10, kwargs={"row_factory": dict_row})
        elif url.startswith("sqlite:///"):
            self.backend = "sqlite"
            self._path = Path(url[len("sqlite:///") :])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._pool = None
            # For in-memory SQLite, keep a single persistent connection so
            # tables created in _init_db() survive across connect() calls.
            if str(self._path) == ":memory:":
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            else:
                self._memory_conn = None
        else:
            raise DatabaseError(f"Unsupported database URL: {url!r}")

    @property
    def serial_pk(self) -> str:
        """Primary key column type for auto-increment."""
        return "BIGSERIAL PRIMARY KEY" if self.backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"

    @property
    def bool_type(self) -> str:
        """Column type for booleans."""
        return "BOOLEAN" if self.backend == "postgresql" else "INTEGER"

    @property
    def bool_true(self) -> int | bool:
        """Literal for TRUE in WHERE clauses."""
        return True if self.backend == "postgresql" else 1

    @property
    def bool_false(self) -> int | bool:
        """Literal for FALSE in WHERE clauses."""
        return False if self.backend == "postgresql" else 0

    @property
    def numeric_cast(self) -> str:
        """SQL cast expression for numeric values (prices)."""
        return "NUMERIC"  # works in both SQLite and PostgreSQL

    @contextmanager
    def connect(self) -> Iterator[Any]:
        """Yield a connection (auto-commit/rollback on exit)."""
        if self.backend == "postgresql":
            conn = self._pool.getconn()
            try:
                yield _PostgresConnection(conn, self._pool)
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)
        else:
            if self._memory_conn is not None:
                # Reuse the persistent in-memory connection
                conn = self._memory_conn
            else:
                conn = sqlite3.connect(self._path)
                conn.row_factory = sqlite3.Row
            try:
                yield _SQLiteConnection(conn)
            except Exception:
                conn.rollback()
                raise
            finally:
                if self._memory_conn is None:
                    conn.close()

    def table_columns(self, conn: Any, table: str) -> set[str]:
        """Return the set of column names for a table."""
        if self.backend == "postgresql":
            rows = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s
                """,
                (table,),
            ).fetchall()
            return {str(row["column_name"]) for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    def ensure_columns(self, conn: Any, table: str, columns: dict[str, str]) -> None:
        """Add missing columns to a table (idempotent)."""
        existing = self.table_columns(conn, table)
        for name, spec in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")

    def close(self) -> None:
        """Close the connection pool (PostgreSQL) or in-memory connection (SQLite)."""
        if self.backend == "postgresql" and self._pool is not None:
            self._pool.close()
        elif self.backend == "sqlite" and self._memory_conn is not None:
            self._memory_conn.close()
