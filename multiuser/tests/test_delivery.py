"""Tests for the DeliveryProcessor (freemium priority, daily caps)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from faresnipe_multiuser.config import AppConfig, DatabaseConfig, FreemiumConfig, ScannerConfig, TelegramConfig
from faresnipe_multiuser.db import Database
from faresnipe_multiuser.delivery import DeliveryProcessor
from faresnipe_multiuser.detector import DetectionConfig
from faresnipe_multiuser.models import ChatType, SubscriberTier
from faresnipe_multiuser.models import FareQuote
from faresnipe_multiuser.storage import FareStore
from faresnipe_multiuser.user_store import UserStore


@pytest.fixture
def db():
    database = Database("sqlite:///:memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return UserStore(db)


@pytest.fixture
def config():
    return AppConfig(
        database=DatabaseConfig(url="sqlite:///:memory:"),
        telegram=TelegramConfig(bot_token="test_token"),
        freemium=FreemiumConfig(
            free_tier_per_subscriber_daily_limit=3,
            free_tier_global_daily_limit=10,
            premium_tier_daily_limit=0,
            free_tier_min_discount=Decimal("0.35"),
            premium_priority=0,
            free_priority=1,
            delivery_batch_size=50,
            delivery_delay_seconds=0,
        ),
        scanner=ScannerConfig(
            provider="mock",
            currency="USD",
            days_ahead_start=7,
            days_ahead_end=30,
            stay_lengths=(7, 14),
            adults=1,
            max_results_per_search=8,
            request_delay_seconds=0,
            scan_interval_minutes=30,
            scan_jitter_seconds=0,
        ),
        detection=DetectionConfig(
            discount_ratio=Decimal("0.35"),
            mistake_fare_ratio=Decimal("0.55"),
            min_history_quotes=4,
            history_days=180,
        ),
    )


class FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def read(self):
        return json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    """Mock urllib opener that records calls and returns success."""

    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []

    def __call__(self, request, timeout=30):
        self.calls.append((request.full_url, request.data))
        return FakeResponse({"ok": True, "result": {"message_id": 1}})


class TestDeliveryProcessor:
    def test_sends_pending_notifications(self, config, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        fare_id = FareStore(store.db).save_quote(
            FareQuote(
                provider="test",
                origin="YYZ",
                destination="GRU",
                departure_date=date(2026, 12, 20),
                return_date=date(2026, 12, 27),
                price=Decimal("499"),
                currency="USD",
                carrier="Test Air",
                booking_url="https://example.test/booking",
            )
        )
        store.enqueue_notification(sub_id, "fp1", fare_id=fare_id, priority=1)

        opener = FakeOpener()
        processor = DeliveryProcessor(config=config, user_store=store, opener=opener)
        sent = processor.run_once()

        assert sent == 1
        assert len(opener.calls) == 1
        # Verify the message was sent to the right chat
        payload = json.loads(opener.calls[0][1])
        assert payload["chat_id"] == 12345
        assert "YYZ → GRU" in payload["text"]
        assert "$" not in payload["text"]
        assert "499 USD" in payload["text"]

        # Queue should be empty now
        pending = store.pending_notifications()
        assert len(pending) == 0

    def test_premium_priority_delivered_first(self, config, store):
        free_sub = store.upsert_subscriber(1, ChatType.PRIVATE)
        premium_sub = store.upsert_subscriber(2, ChatType.PRIVATE)
        store.set_subscriber_tier(2, SubscriberTier.PREMIUM)

        # Enqueue free first, then premium
        store.enqueue_notification(free_sub, "fp_free", fare_id=1, priority=1)
        store.enqueue_notification(premium_sub, "fp_premium", fare_id=2, priority=0)

        opener = FakeOpener()
        processor = DeliveryProcessor(config=config, user_store=store, opener=opener)
        sent = processor.run_once()

        assert sent == 2
        # Premium (priority 0) should be sent first
        first_payload = json.loads(opener.calls[0][1])
        assert first_payload["chat_id"] == 2  # premium subscriber

    def test_free_tier_daily_cap(self, config, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)

        # Enqueue 5 notifications for a free subscriber (cap is 3)
        for i in range(5):
            store.enqueue_notification(sub_id, f"fp{i}", fare_id=i, priority=1)

        opener = FakeOpener()
        processor = DeliveryProcessor(config=config, user_store=store, opener=opener)
        sent = processor.run_once()

        # Only 3 should be sent (per-subscriber daily cap)
        assert sent == 3
        assert len(opener.calls) == 3

    def test_premium_unlimited(self, config, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.set_subscriber_tier(12345, SubscriberTier.PREMIUM)

        # Enqueue 5 notifications for premium (unlimited)
        for i in range(5):
            store.enqueue_notification(sub_id, f"fp{i}", fare_id=i, priority=0)

        opener = FakeOpener()
        processor = DeliveryProcessor(config=config, user_store=store, opener=opener)
        sent = processor.run_once()

        assert sent == 5

    def test_global_free_cap(self, config, store):
        # Set global cap to 2
        config = AppConfig(
            database=config.database,
            telegram=config.telegram,
            freemium=FreemiumConfig(
                free_tier_per_subscriber_daily_limit=10,
                free_tier_global_daily_limit=2,
                premium_tier_daily_limit=0,
                free_tier_min_discount=Decimal("0.35"),
                premium_priority=0,
                free_priority=1,
                delivery_batch_size=50,
                delivery_delay_seconds=0,
            ),
            scanner=config.scanner,
            detection=config.detection,
        )

        sub1 = store.upsert_subscriber(1, ChatType.PRIVATE)
        sub2 = store.upsert_subscriber(2, ChatType.PRIVATE)

        store.enqueue_notification(sub1, "fp1", fare_id=1, priority=1)
        store.enqueue_notification(sub2, "fp2", fare_id=2, priority=1)
        store.enqueue_notification(sub1, "fp3", fare_id=3, priority=1)

        opener = FakeOpener()
        processor = DeliveryProcessor(config=config, user_store=store, opener=opener)
        sent = processor.run_once()

        # Only 2 should be sent (global cap)
        assert sent == 2

    def test_failed_delivery_marks_failed(self, config, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1)

        class FailingOpener:
            def __call__(self, request, timeout=30):
                raise Exception("connection refused")

        processor = DeliveryProcessor(config=config, user_store=store, opener=FailingOpener())
        sent = processor.run_once()

        assert sent == 0
        # Notification should be marked as failed
        with store.db.connect() as conn:
            row = conn.execute(
                "SELECT status, error FROM notification_queue WHERE id = 1"
            ).fetchone()
            assert row["status"] == "failed"
            assert "connection refused" in row["error"]
