"""Delivery queue processor for faresnipe-multiuser.

Processes pending notifications from the queue, sending them via Telegram
with freemium priority: premium subscribers (priority 0) are delivered
first, free subscribers (priority 1) after, with daily caps.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .config import AppConfig
from .models import SubscriberTier
from .user_store import UserStore

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class DeliveryError(Exception):
    """Raised for delivery errors."""


class DeliveryProcessor:
    """Processes the notification queue with freemium priority."""

    def __init__(
        self,
        config: AppConfig,
        user_store: UserStore,
        opener: Callable[..., object] = urllib.request.urlopen,
        error_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.user_store = user_store
        self.opener = opener
        self._error_sink = error_sink or (lambda msg: print(f"delivery: {msg}"))

    def run_once(self) -> int:
        """Process one batch of pending notifications. Returns count sent."""
        batch = self.user_store.pending_notifications(
            limit=self.config.freemium.delivery_batch_size
        )
        sent = 0
        for notification in batch:
            if not self._can_send(notification):
                continue
            try:
                self._send_telegram(
                    chat_id=int(notification["telegram_chat_id"]),
                    text=self._format_notification(notification),
                )
                self.user_store.mark_notification_sent(int(notification["id"]))
                sent += 1
            except DeliveryError as exc:
                self._error_sink(f"delivery failed for notification {notification['id']}: {exc}")
                self.user_store.mark_notification_failed(int(notification["id"]), str(exc))
            time.sleep(self.config.freemium.delivery_delay_seconds)
        return sent

    def run(self, cycles: int | None = None) -> None:
        """Run the delivery loop. If cycles is None, run forever."""
        cycle = 0
        while cycles is None or cycle < cycles:
            cycle += 1
            try:
                sent = self.run_once()
                if sent == 0:
                    time.sleep(self.config.freemium.delivery_delay_seconds * 10)
            except Exception as exc:
                self._error_sink(f"delivery cycle error: {exc}")
                time.sleep(self.config.freemium.delivery_delay_seconds * 10)

    # ------------------------------------------------------------------
    # Freemium logic
    # ------------------------------------------------------------------

    def _can_send(self, notification: dict[str, Any]) -> bool:
        """Check if a notification can be sent based on tier and daily caps."""
        subscriber_id = int(notification["subscriber_id"])
        tier = SubscriberTier(str(notification["tier"]))

        if tier == SubscriberTier.PREMIUM:
            limit = self.config.freemium.premium_tier_daily_limit
            if limit > 0:
                count = self.user_store.daily_notification_count(subscriber_id)
                if count >= limit:
                    return False
            return True

        # Free tier
        per_subscriber_limit = self.config.freemium.free_tier_per_subscriber_daily_limit
        count = self.user_store.daily_notification_count(subscriber_id)
        if count >= per_subscriber_limit:
            return False

        global_limit = self.config.freemium.free_tier_global_daily_limit
        global_count = self.user_store.global_daily_notification_count(SubscriberTier.FREE)
        if global_count >= global_limit:
            return False

        return True

    # ------------------------------------------------------------------
    # Telegram API
    # ------------------------------------------------------------------

    def _send_telegram(self, chat_id: int, text: str) -> None:
        """Send a message via Telegram Bot API."""
        url = API_BASE.format(token=self.config.telegram.bot_token, method="sendMessage")
        data = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise DeliveryError(f"Telegram API error {exc.code}: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise DeliveryError(f"Telegram API request failed: {exc}") from exc

        if not body.get("ok"):
            raise DeliveryError(f"Telegram API error: {body.get('description', 'unknown')}")

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_notification(self, notification: dict[str, Any]) -> str:
        """Format a notification for Telegram delivery."""
        # The notification_queue stores fare_id; we need to fetch the fare details.
        # For now, format with available data. In production, join with fares table.
        return (
            f"✈️ Promoção encontrada!\n\n"
            f"Rota: {notification.get('origin', '?')} → {notification.get('destination', '?')}\n"
            f"Preço: {notification.get('price', '?')} {notification.get('currency', '?')}\n"
            f"Data: {notification.get('departure_date', '?')}\n"
            f"Companhia: {notification.get('carrier', '?')}\n"
            f"Reservar: {notification.get('booking_url', '?')}"
        )