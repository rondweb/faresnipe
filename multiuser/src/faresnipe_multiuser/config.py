"""Configuration loading for faresnipe-multiuser."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .detector import DetectionConfig


@dataclass(frozen=True)
class DatabaseConfig:
    url: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    polling_timeout: int = 30
    polling_interval: float = 1.0


@dataclass(frozen=True)
class FreemiumConfig:
    free_tier_per_subscriber_daily_limit: int = 3
    free_tier_global_daily_limit: int = 100
    premium_tier_daily_limit: int = 0  # 0 = unlimited
    free_tier_min_discount: Decimal = Decimal("0.35")
    premium_priority: int = 0
    free_priority: int = 1
    delivery_batch_size: int = 50
    delivery_delay_seconds: float = 0.5


@dataclass(frozen=True)
class ScannerConfig:
    provider: str
    currency: str
    days_ahead_start: int
    days_ahead_end: int
    stay_lengths: tuple[int, ...]
    adults: int
    max_results_per_search: int
    request_delay_seconds: float
    scan_interval_minutes: float
    scan_jitter_seconds: float


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    telegram: TelegramConfig
    freemium: FreemiumConfig
    scanner: ScannerConfig
    detection: DetectionConfig


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    database_data = data.get("database", {})
    telegram_data = data.get("telegram", {})
    freemium_data = data.get("freemium", {})
    scanner_data = data.get("scanner", {})
    detection_data = data.get("detection", {})

    # Database URL: env var overrides config
    database_url = os.environ.get(
        "FARESNIPE_MULTI_DATABASE_URL",
        str(database_data.get("url", "sqlite:///data/faresnipe_multiuser.sqlite3")),
    )

    # Telegram bot token: env var overrides config
    bot_token = os.environ.get("FARESNIPE_MULTI_TELEGRAM_BOT_TOKEN")
    if bot_token is None:
        # Keep compatibility with the single-user service's shared .env file.
        bot_token = os.environ.get("FARESNIPE_TELEGRAM_BOT_TOKEN")
    if bot_token is None:
        bot_token = str(telegram_data.get("bot_token", ""))
    bot_token = bot_token.strip()

    return AppConfig(
        database=DatabaseConfig(url=database_url),
        telegram=TelegramConfig(
            bot_token=bot_token,
            polling_timeout=int(telegram_data.get("polling_timeout", 30)),
            polling_interval=float(telegram_data.get("polling_interval", 1.0)),
        ),
        freemium=FreemiumConfig(
            free_tier_per_subscriber_daily_limit=int(
                freemium_data.get("free_tier_per_subscriber_daily_limit", 3)
            ),
            free_tier_global_daily_limit=int(
                freemium_data.get("free_tier_global_daily_limit", 100)
            ),
            premium_tier_daily_limit=int(freemium_data.get("premium_tier_daily_limit", 0)),
            free_tier_min_discount=_decimal(
                freemium_data.get("free_tier_min_discount", "0.35")
            ),
            premium_priority=int(freemium_data.get("premium_priority", 0)),
            free_priority=int(freemium_data.get("free_priority", 1)),
            delivery_batch_size=int(freemium_data.get("delivery_batch_size", 50)),
            delivery_delay_seconds=float(freemium_data.get("delivery_delay_seconds", 0.5)),
        ),
        scanner=ScannerConfig(
            provider=str(scanner_data.get("provider", "google_flights_structured")),
            currency=str(scanner_data.get("currency", "USD")).upper(),
            days_ahead_start=int(scanner_data.get("days_ahead_start", 7)),
            days_ahead_end=int(scanner_data.get("days_ahead_end", 120)),
            stay_lengths=tuple(int(v) for v in scanner_data.get("stay_lengths", [7, 14])),
            adults=int(scanner_data.get("adults", 1)),
            max_results_per_search=int(scanner_data.get("max_results_per_search", 8)),
            request_delay_seconds=float(scanner_data.get("request_delay_seconds", 0.3)),
            scan_interval_minutes=float(scanner_data.get("scan_interval_minutes", 30)),
            scan_jitter_seconds=float(scanner_data.get("scan_jitter_seconds", 60)),
        ),
        detection=DetectionConfig(
            discount_ratio=_decimal(detection_data.get("discount_ratio", "0.35")),
            mistake_fare_ratio=_decimal(detection_data.get("mistake_fare_ratio", "0.55")),
            min_history_quotes=int(detection_data.get("min_history_quotes", 4)),
            history_days=int(detection_data.get("history_days", 180)),
        ),
    )


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))