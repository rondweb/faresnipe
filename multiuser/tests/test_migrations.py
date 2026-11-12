"""Tests for the multi-user database migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_sqlite_migrations_create_auto_incrementing_subscriber_id(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "multiuser.sqlite3"
    monkeypatch.setenv("FARESNIPE_MULTI_DATABASE_URL", f"sqlite:///{database_path}")

    alembic_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(alembic_config, "head")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO subscribers (
                telegram_chat_id, chat_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (12345, "private", "now", "now"),
        )
        subscriber_id = connection.execute(
            "SELECT id FROM subscribers WHERE telegram_chat_id = ?",
            (12345,),
        ).fetchone()[0]

    assert subscriber_id == 1
