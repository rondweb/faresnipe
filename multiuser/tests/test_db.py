"""Tests for the Database abstraction layer."""

from __future__ import annotations

import pytest

from faresnipe_multiuser.db import Database, _convert_placeholders


class TestPlaceholderConversion:
    def test_converts_simple_question_marks(self):
        assert _convert_placeholders("SELECT ? FROM ?") == "SELECT %s FROM %s"

    def test_does_not_convert_inside_string_literals(self):
        query = "SELECT * FROM fares WHERE booking_url LIKE 'https://example.com?x=1' AND price = ?"
        result = _convert_placeholders(query)
        assert "?x=1" in result  # URL ? preserved
        assert result.count("%s") == 1  # only the parameter placeholder converted

    def test_handles_escaped_quotes(self):
        query = "SELECT ? WHERE name = 'it''s' AND value = ?"
        result = _convert_placeholders(query)
        assert result.count("%s") == 2

    def test_multiline_query(self):
        query = """
        SELECT id FROM fares
        WHERE origin = ? AND destination = ?
          AND booking_url = 'https://x.com?a=1&b=2'
        """
        result = _convert_placeholders(query)
        assert result.count("%s") == 2
        assert "?a=1" in result


class TestSQLiteDatabase:
    def setup_method(self):
        self.db = Database("sqlite:///:memory:")

    def teardown_method(self):
        self.db.close()

    def test_connect_execute(self):
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
            conn.execute("INSERT INTO t (name) VALUES (?)", ("test",))
            conn.execute("INSERT INTO t (name) VALUES (?)", ("test2",))
            conn.commit()
            rows = conn.execute("SELECT * FROM t ORDER BY id").fetchall()
            assert len(rows) == 2
            assert rows[0]["name"] == "test"
            assert rows[1]["name"] == "test2"

    def test_connect_rollback_on_exception(self):
        # Create the table first (DDL auto-commits in SQLite)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
            conn.commit()

        with pytest.raises(RuntimeError):
            with self.db.connect() as conn:
                conn.execute("INSERT INTO t (name) VALUES (?)", ("test",))
                conn.execute("INSERT INTO t (name) VALUES (?)", ("test2",))
                raise RuntimeError("boom")  # No commit — should rollback

        # Since we never committed, rollback should discard the INSERTs
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM t").fetchall()
            assert len(rows) == 0

    def test_connect_commits_on_success(self):
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE t2 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
            conn.execute("INSERT INTO t2 (name) VALUES (?)", ("test",))

        # Since the context manager auto-commits on success, table should exist
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='t2'"
            ).fetchall()
            assert len(rows) == 1

    def test_serial_pk_sqlite(self):
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in self.db.serial_pk

    def test_bool_type_sqlite(self):
        assert self.db.bool_type == "INTEGER"
        assert self.db.bool_true == 1
        assert self.db.bool_false == 0

    def test_table_columns(self):
        with self.db.connect() as conn:
            conn.execute(
                "CREATE TABLE test_cols (id INTEGER PRIMARY KEY, name TEXT, enabled INTEGER)"
            )
            conn.commit()
            cols = self.db.table_columns(conn, "test_cols")
            assert cols == {"id", "name", "enabled"}

    def test_ensure_columns(self):
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE test_ensure (id INTEGER PRIMARY KEY, name TEXT)")
            conn.commit()
            self.db.ensure_columns(conn, "test_ensure", {"email": "TEXT", "age": "INTEGER"})
            cols = self.db.table_columns(conn, "test_ensure")
            assert "email" in cols
            assert "age" in cols
            # Idempotent
            self.db.ensure_columns(conn, "test_ensure", {"email": "TEXT"})
            cols2 = self.db.table_columns(conn, "test_ensure")
            assert cols2 == cols