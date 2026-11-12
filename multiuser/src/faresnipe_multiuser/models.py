"""Data models for faresnipe-multiuser."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class TripKind(str, Enum):
    ROUND_TRIP = "round_trip"
    ONE_WAY = "one_way"


class SubscriberTier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"


class ChatType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class SearchQuery:
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    adults: int
    currency: str

    @property
    def trip_kind(self) -> TripKind:
        return TripKind.ROUND_TRIP if self.return_date else TripKind.ONE_WAY


@dataclass(frozen=True)
class FareQuote:
    provider: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    price: Decimal
    currency: str
    carrier: str | None = None
    flight_numbers: tuple[str, ...] = ()
    stops: int | None = None
    duration: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    booking_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def trip_kind(self) -> TripKind:
        return TripKind.ROUND_TRIP if self.return_date else TripKind.ONE_WAY


@dataclass(frozen=True)
class Baseline:
    median_price: Decimal | None
    min_price: Decimal | None
    quote_count: int


@dataclass(frozen=True)
class DealAlert:
    quote: FareQuote
    reasons: tuple[str, ...]
    severity: str
    baseline: Baseline
    discount_ratio: Decimal | None = None


@dataclass(frozen=True)
class Subscriber:
    id: int
    telegram_chat_id: int
    chat_type: ChatType
    chat_title: str | None
    username: str | None
    first_name: str | None
    tier: SubscriberTier
    is_active: bool
    is_approved: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Subscription:
    id: int
    subscriber_id: int
    origin: str
    destination: str
    departure_date: date
    departure_flex_days: int
    return_date: date | None
    max_price: Decimal | None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class QueuedNotification:
    id: int
    subscriber_id: int
    alert_fingerprint: str
    fare_id: int | None
    priority: int
    status: NotificationStatus
    created_at: datetime
    sent_at: datetime | None
    error: str | None