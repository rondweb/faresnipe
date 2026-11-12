"""User/subscriber storage layer for faresnipe-multiuser.

Handles subscribers (private users and Telegram groups), their route
subscriptions, the notification delivery queue, and bot conversation state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .db import Database
from .models import (
    ChatType,
    Subscriber,
    SubscriberTier,
    Subscription,
)


class UserStore:
    """Stores subscribers, subscriptions, notification queue, and conversation state."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._init_db()

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------

    def upsert_subscriber(
        self,
        telegram_chat_id: int,
        chat_type: ChatType,
        chat_title: str | None = None,
        username: str | None = None,
        first_name: str | None = None,
        tier: SubscriberTier = SubscriberTier.FREE,
        is_approved: bool | None = None,
    ) -> int:
        """Insert or update a subscriber by telegram_chat_id. Returns subscriber id."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Groups require approval; private chats are auto-approved.
        if is_approved is None:
            is_approved = chat_type == ChatType.PRIVATE

        returning = " RETURNING id" if self.db.backend == "postgresql" else ""
        with self.db.connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO subscribers (
                    telegram_chat_id, chat_type, chat_title, username, first_name,
                    tier, is_active, is_approved, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    chat_type = excluded.chat_type,
                    chat_title = COALESCE(excluded.chat_title, subscribers.chat_title),
                    username = COALESCE(excluded.username, subscribers.username),
                    first_name = COALESCE(excluded.first_name, subscribers.first_name),
                    is_active = {self.db.bool_true},
                    updated_at = excluded.updated_at
                {returning}
                """,
                (
                    telegram_chat_id, chat_type.value, chat_title, username, first_name,
                    tier.value, self.db.bool_true, is_approved, now, now,
                ),
            )
            conn.commit()
            if self.db.backend == "postgresql":
                row = cur.fetchone()
                return int(row["id"]) if row else 0
            return int(conn.lastrowid)

    def get_subscriber(self, telegram_chat_id: int) -> Subscriber | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscribers WHERE telegram_chat_id = ?",
                (telegram_chat_id,),
            ).fetchone()
        return _row_to_subscriber(row) if row else None

    def get_subscriber_by_id(self, subscriber_id: int) -> Subscriber | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscribers WHERE id = ?",
                (subscriber_id,),
            ).fetchone()
        return _row_to_subscriber(row) if row else None

    def set_subscriber_tier(self, telegram_chat_id: int, tier: SubscriberTier) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscribers SET tier = ?, updated_at = ? WHERE telegram_chat_id = ?",
                (tier.value, now, telegram_chat_id),
            )
            conn.commit()

    def set_subscriber_active(self, telegram_chat_id: int, is_active: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscribers SET is_active = ?, updated_at = ? WHERE telegram_chat_id = ?",
                (self.db.bool_true if is_active else self.db.bool_false, now, telegram_chat_id),
            )
            conn.commit()

    def set_subscriber_approved(self, telegram_chat_id: int, is_approved: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscribers SET is_approved = ?, updated_at = ? WHERE telegram_chat_id = ?",
                (self.db.bool_true if is_approved else self.db.bool_false, now, telegram_chat_id),
            )
            conn.commit()

    def list_subscribers(self, active_only: bool = True) -> list[Subscriber]:
        where = f"WHERE is_active = {self.db.bool_true}" if active_only else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM subscribers {where} ORDER BY id").fetchall()
        return [_row_to_subscriber(row) for row in rows]

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def add_subscription(
        self,
        subscriber_id: int,
        origin: str,
        destination: str,
        departure_date: date,
        departure_flex_days: int = 7,
        return_date: date | None = None,
        max_price: Decimal | None = None,
    ) -> int:
        """Add a route subscription for a subscriber. Returns subscription id."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        returning = " RETURNING id" if self.db.backend == "postgresql" else ""
        with self.db.connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO subscriptions (
                    subscriber_id, origin, destination, departure_date,
                    departure_flex_days, return_date, max_price, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                {returning}
                """,
                (
                    subscriber_id, origin.upper(), destination.upper(),
                    departure_date.isoformat(), departure_flex_days,
                    return_date.isoformat() if return_date else None,
                    str(max_price) if max_price is not None else None,
                    self.db.bool_true, now,
                ),
            )
            conn.commit()
            if self.db.backend == "postgresql":
                row = cur.fetchone()
                return int(row["id"]) if row else 0
            return int(conn.lastrowid)

    def list_subscriptions(self, subscriber_id: int, active_only: bool = True) -> list[Subscription]:
        where = "subscriber_id = ?"
        params: list[Any] = [subscriber_id]
        if active_only:
            where += f" AND is_active = {self.db.bool_true}"
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM subscriptions WHERE {where} ORDER BY id",
                tuple(params),
            ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    def remove_subscription(self, subscription_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET is_active = ? WHERE id = ?",
                (self.db.bool_false, subscription_id),
            )
            conn.commit()

    def active_subscription_routes(self) -> list[dict[str, Any]]:
        """Return unique (origin, destination) pairs from all active subscriptions."""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT origin, destination
                FROM subscriptions
                WHERE is_active = {self.db.bool_true}
                ORDER BY origin, destination
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def subscriptions_matching(
        self, origin: str, destination: str, departure_date: date
    ) -> list[Subscription]:
        """Find active subscriptions matching a route and date (with flex days)."""
        # Date difference: SQLite uses julianday(), PostgreSQL uses EXTRACT(EPOCH FROM ...).
        date_diff = (
            "ABS(EXTRACT(EPOCH FROM (s.departure_date::date - ?::date)) / 86400)"
            if self.db.backend == "postgresql"
            else "ABS(julianday(s.departure_date) - julianday(?))"
        )
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.* FROM subscriptions s
                JOIN subscribers sub ON s.subscriber_id = sub.id
                WHERE s.origin = ? AND s.destination = ?
                  AND s.is_active = {self.db.bool_true}
                  AND sub.is_active = {self.db.bool_true}
                  AND sub.is_approved = {self.db.bool_true}
                  AND {date_diff} <= s.departure_flex_days
                ORDER BY s.id
                """,
                (origin.upper(), destination.upper(), departure_date.isoformat()),
            ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    # ------------------------------------------------------------------
    # Notification queue
    # ------------------------------------------------------------------

    def enqueue_notification(
        self,
        subscriber_id: int,
        alert_fingerprint: str,
        fare_id: int | None,
        priority: int,
    ) -> bool:
        """Enqueue a notification for a subscriber. Returns False if duplicate."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO notification_queue (
                        subscriber_id, alert_fingerprint, fare_id, priority,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?)
                    """,
                    (subscriber_id, alert_fingerprint, fare_id, priority, now),
                )
                conn.commit()
                return True
            except Exception:
                # Duplicate (UNIQUE constraint on subscriber_id + alert_fingerprint)
                conn.rollback()
                return False

    def pending_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get pending notifications ordered by priority (0=premium first) then created_at."""
        with self.db.connect() as conn:
            if self.db.backend == "postgresql":
                fare_table = conn.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'fares'
                    """
                ).fetchone()
            else:
                fare_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fares'"
                ).fetchone()
            fare_fields = (
                "f.origin, f.destination, f.price, f.currency, "
                "f.departure_date, f.carrier, f.booking_url"
                if fare_table
                else "NULL AS origin, NULL AS destination, NULL AS price, "
                "NULL AS currency, NULL AS departure_date, NULL AS carrier, "
                "NULL AS booking_url"
            )
            fare_join = "LEFT JOIN fares f ON nq.fare_id = f.id" if fare_table else ""
            rows = conn.execute(
                f"""
                SELECT nq.*, s.telegram_chat_id, s.chat_type, s.chat_title, s.tier,
                       {fare_fields}
                FROM notification_queue nq
                JOIN subscribers s ON nq.subscriber_id = s.id
                {fare_join}
                WHERE nq.status = 'pending'
                  AND s.is_active = ?
                  AND s.is_approved = ?
                ORDER BY nq.priority ASC, nq.created_at ASC
                LIMIT ?
                """,
                (self.db.bool_true, self.db.bool_true, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notification_sent(self, notification_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE notification_queue SET status = 'sent', sent_at = ? WHERE id = ?",
                (now, notification_id),
            )
            conn.commit()

    def mark_notification_failed(self, notification_id: int, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE notification_queue SET status = 'failed', error = ? WHERE id = ?",
                (error, notification_id),
            )
            conn.commit()

    def daily_notification_count(self, subscriber_id: int) -> int:
        """Count notifications sent to a subscriber today."""
        today = date.today().isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM notification_queue
                WHERE subscriber_id = ? AND status = 'sent' AND sent_at >= ?
                """,
                (subscriber_id, today),
            ).fetchone()
        return int(row["count"]) if row else 0

    def global_daily_notification_count(self, tier: SubscriberTier) -> int:
        """Count notifications sent today across all subscribers of a tier."""
        today = date.today().isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM notification_queue nq
                JOIN subscribers s ON nq.subscriber_id = s.id
                WHERE nq.status = 'sent' AND nq.sent_at >= ? AND s.tier = ?
                """,
                (today, tier.value),
            ).fetchone()
        return int(row["count"]) if row else 0

    # ------------------------------------------------------------------
    # Conversation state
    # ------------------------------------------------------------------

    def get_conversation_state(self, telegram_chat_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT state FROM conversation_state WHERE telegram_chat_id = ?",
                (telegram_chat_id,),
            ).fetchone()
        if not row:
            return None
        import json

        return json.loads(row["state"])

    def set_conversation_state(self, telegram_chat_id: int, state: dict[str, Any] | None) -> None:
        import json

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            if state is None:
                conn.execute(
                    "DELETE FROM conversation_state WHERE telegram_chat_id = ?",
                    (telegram_chat_id,),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO conversation_state (telegram_chat_id, state, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(telegram_chat_id) DO UPDATE SET
                        state = excluded.state,
                        updated_at = excluded.updated_at
                    """,
                    (telegram_chat_id, json.dumps(state), now),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS subscribers (
                    id {self.db.serial_pk},
                    telegram_chat_id BIGINT NOT NULL UNIQUE,
                    chat_type TEXT NOT NULL DEFAULT 'private',
                    chat_title TEXT,
                    username TEXT,
                    first_name TEXT,
                    tier TEXT NOT NULL DEFAULT 'free',
                    is_active {self.db.bool_type} NOT NULL DEFAULT {self.db.bool_true},
                    is_approved {self.db.bool_type} NOT NULL DEFAULT {self.db.bool_true},
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id {self.db.serial_pk},
                    subscriber_id INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    departure_date TEXT NOT NULL,
                    departure_flex_days INTEGER NOT NULL DEFAULT 7,
                    return_date TEXT,
                    max_price TEXT,
                    is_active {self.db.bool_type} NOT NULL DEFAULT {self.db.bool_true},
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS notification_queue (
                    id {self.db.serial_pk},
                    subscriber_id INTEGER NOT NULL,
                    alert_fingerprint TEXT NOT NULL,
                    fare_id INTEGER,
                    priority INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    error TEXT,
                    UNIQUE(subscriber_id, alert_fingerprint),
                    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS conversation_state (
                    id {self.db.serial_pk},
                    telegram_chat_id BIGINT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()


def _row_to_subscriber(row: Any) -> Subscriber:
    data = dict(row)
    return Subscriber(
        id=int(data["id"]),
        telegram_chat_id=int(data["telegram_chat_id"]),
        chat_type=ChatType(str(data["chat_type"])),
        chat_title=data.get("chat_title"),
        username=data.get("username"),
        first_name=data.get("first_name"),
        tier=SubscriberTier(str(data["tier"])),
        is_active=bool(data["is_active"]),
        is_approved=bool(data["is_approved"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
        updated_at=datetime.fromisoformat(str(data["updated_at"])),
    )


def _row_to_subscription(row: Any) -> Subscription:
    data = dict(row)
    return Subscription(
        id=int(data["id"]),
        subscriber_id=int(data["subscriber_id"]),
        origin=str(data["origin"]),
        destination=str(data["destination"]),
        departure_date=date.fromisoformat(str(data["departure_date"])),
        departure_flex_days=int(data["departure_flex_days"]),
        return_date=date.fromisoformat(str(data["return_date"])) if data.get("return_date") else None,
        max_price=Decimal(str(data["max_price"])) if data.get("max_price") else None,
        is_active=bool(data["is_active"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
    )
