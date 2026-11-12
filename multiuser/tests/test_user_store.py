"""Tests for the UserStore (subscribers, subscriptions, queue)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from faresnipe_multiuser.db import Database
from faresnipe_multiuser.models import ChatType, SubscriberTier
from faresnipe_multiuser.user_store import UserStore


@pytest.fixture
def db():
    database = Database("sqlite:///:memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return UserStore(db)


class TestSubscribers:
    def test_upsert_private_subscriber(self, store):
        sub_id = store.upsert_subscriber(
            telegram_chat_id=12345,
            chat_type=ChatType.PRIVATE,
            username="testuser",
            first_name="Test",
        )
        assert sub_id > 0

        sub = store.get_subscriber(12345)
        assert sub is not None
        assert sub.telegram_chat_id == 12345
        assert sub.chat_type == ChatType.PRIVATE
        assert sub.tier == SubscriberTier.FREE
        assert sub.is_active is True
        assert sub.is_approved is True  # private chats auto-approved

    def test_upsert_group_requires_approval(self, store):
        store.upsert_subscriber(
            telegram_chat_id=-10012345,
            chat_type=ChatType.GROUP,
            chat_title="Test Group",
        )
        sub = store.get_subscriber(-10012345)
        assert sub is not None
        assert sub.chat_type == ChatType.GROUP
        assert sub.is_approved is False  # groups need manual approval

    def test_upsert_is_idempotent(self, store):
        sub_id1 = store.upsert_subscriber(
            telegram_chat_id=12345,
            chat_type=ChatType.PRIVATE,
            username="user1",
        )
        sub_id2 = store.upsert_subscriber(
            telegram_chat_id=12345,
            chat_type=ChatType.PRIVATE,
            username="user2",
        )
        assert sub_id1 == sub_id2

        sub = store.get_subscriber(12345)
        assert sub.username == "user2"  # updated

    def test_set_tier(self, store):
        store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.set_subscriber_tier(12345, SubscriberTier.PREMIUM)
        sub = store.get_subscriber(12345)
        assert sub.tier == SubscriberTier.PREMIUM

    def test_set_active(self, store):
        store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.set_subscriber_active(12345, False)
        sub = store.get_subscriber(12345)
        assert sub.is_active is False

    def test_list_subscribers(self, store):
        store.upsert_subscriber(1, ChatType.PRIVATE)
        store.upsert_subscriber(2, ChatType.PRIVATE)
        store.upsert_subscriber(3, ChatType.PRIVATE)
        store.set_subscriber_active(3, False)

        active = store.list_subscribers(active_only=True)
        assert len(active) == 2

        all_subs = store.list_subscribers(active_only=False)
        assert len(all_subs) == 3


class TestSubscriptions:
    def test_add_and_list(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        sub_id2 = store.add_subscription(
            subscriber_id=sub_id,
            origin="YUL",
            destination="GRU",
            departure_date=date(2026, 9, 15),
            departure_flex_days=7,
            max_price=Decimal("500"),
        )
        assert sub_id2 > 0

        subs = store.list_subscriptions(sub_id)
        assert len(subs) == 1
        assert subs[0].origin == "YUL"
        assert subs[0].destination == "GRU"
        assert subs[0].departure_date == date(2026, 9, 15)
        assert subs[0].max_price == Decimal("500")

    def test_remove_subscription(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        sub_id2 = store.add_subscription(
            subscriber_id=sub_id,
            origin="YUL",
            destination="GRU",
            departure_date=date(2026, 9, 15),
        )
        store.remove_subscription(sub_id2)

        subs = store.list_subscriptions(sub_id)
        assert len(subs) == 0

    def test_active_subscription_routes(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.add_subscription(sub_id, "YUL", "GRU", date(2026, 9, 15))
        store.add_subscription(sub_id, "YUL", "GRU", date(2026, 10, 1))
        store.add_subscription(sub_id, "YYZ", "CGN", date(2026, 9, 20))

        routes = store.active_subscription_routes()
        assert len(routes) == 2
        route_set = {(r["origin"], r["destination"]) for r in routes}
        assert ("YUL", "GRU") in route_set
        assert ("YYZ", "CGN") in route_set

    def test_subscriptions_matching_with_flex(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.add_subscription(
            sub_id, "YUL", "GRU", date(2026, 9, 15), departure_flex_days=7
        )

        # Within flex range
        matches = store.subscriptions_matching("YUL", "GRU", date(2026, 9, 18))
        assert len(matches) == 1

        # Outside flex range
        matches = store.subscriptions_matching("YUL", "GRU", date(2026, 10, 1))
        assert len(matches) == 0

        # Wrong route
        matches = store.subscriptions_matching("YUL", "CGN", date(2026, 9, 18))
        assert len(matches) == 0


class TestNotificationQueue:
    def test_enqueue_and_pending(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fingerprint1", fare_id=1, priority=1)

        pending = store.pending_notifications()
        assert len(pending) == 1
        assert pending[0]["alert_fingerprint"] == "fingerprint1"
        assert pending[0]["priority"] == 1

    def test_enqueue_deduplicates(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        assert store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1) is True
        assert store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1) is False

        pending = store.pending_notifications()
        assert len(pending) == 1

    def test_priority_ordering(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1)
        store.enqueue_notification(sub_id, "fp2", fare_id=2, priority=0)

        pending = store.pending_notifications()
        assert len(pending) == 2
        assert pending[0]["priority"] == 0  # premium first
        assert pending[1]["priority"] == 1

    def test_mark_sent(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1)

        pending = store.pending_notifications()
        assert len(pending) == 1
        store.mark_notification_sent(pending[0]["id"])

        pending = store.pending_notifications()
        assert len(pending) == 0

    def test_mark_failed(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1)

        pending = store.pending_notifications()
        store.mark_notification_failed(pending[0]["id"], "Telegram API error 403")

        pending = store.pending_notifications()
        assert len(pending) == 0

    def test_daily_notification_count(self, store):
        sub_id = store.upsert_subscriber(12345, ChatType.PRIVATE)
        store.enqueue_notification(sub_id, "fp1", fare_id=1, priority=1)
        store.enqueue_notification(sub_id, "fp2", fare_id=2, priority=1)

        pending = store.pending_notifications()
        for n in pending:
            store.mark_notification_sent(n["id"])

        count = store.daily_notification_count(sub_id)
        assert count == 2

    def test_global_daily_notification_count(self, store):
        sub1 = store.upsert_subscriber(1, ChatType.PRIVATE)
        sub2 = store.upsert_subscriber(2, ChatType.PRIVATE)

        store.enqueue_notification(sub1, "fp1", fare_id=1, priority=1)
        store.enqueue_notification(sub2, "fp2", fare_id=2, priority=1)

        pending = store.pending_notifications()
        for n in pending:
            store.mark_notification_sent(n["id"])

        count = store.global_daily_notification_count(SubscriberTier.FREE)
        assert count == 2


class TestConversationState:
    def test_set_and_get(self, store):
        store.set_conversation_state(12345, {"state": "await_origin", "data": {}})
        state = store.get_conversation_state(12345)
        assert state == {"state": "await_origin", "data": {}}

    def test_clear(self, store):
        store.set_conversation_state(12345, {"state": "await_origin", "data": {}})
        store.set_conversation_state(12345, None)
        state = store.get_conversation_state(12345)
        assert state is None