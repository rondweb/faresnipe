"""Flight scanner for faresnipe-multiuser.

Scans routes from subscriber subscriptions, detects deals, and enqueues
notifications to the delivery queue with freemium priority.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

from .config import AppConfig
from .db import Database
from .detector import DealDetector, RouteThreshold, alert_fingerprint
from .models import DealAlert, SearchQuery, SubscriberTier
from .providers import build_providers
from .storage import FareStore
from .user_store import UserStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanStats:
    searches: int = 0
    quotes: int = 0
    alerts: int = 0
    failures: int = 0
    enqueued: int = 0


class FlightScanner:
    """Scans subscription routes and enqueues deal notifications."""

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        user_store: UserStore,
        provider_names: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.user_store = user_store
        self.store = FareStore(db)
        self.detector = DealDetector(config.detection)
        names = provider_names or (config.scanner.provider,)
        self.providers = build_providers(names)

    def run_once(self, limit_searches: int | None = None) -> ScanStats:
        """Run one scan cycle over all active subscription routes."""
        searches = 0
        quotes_count = 0
        alerts_count = 0
        failures_count = 0
        enqueued_count = 0

        routes = self.user_store.active_subscription_routes()
        if not routes:
            LOGGER.info("No active subscription routes to scan.")
            return ScanStats()

        for route in routes:
            origin = str(route["origin"])
            destination = str(route["destination"])
            queries = self._queries_for_route(origin, destination)

            for query in queries:
                if limit_searches is not None and searches >= limit_searches:
                    return ScanStats(
                        searches=searches,
                        quotes=quotes_count,
                        alerts=alerts_count,
                        failures=failures_count,
                        enqueued=enqueued_count,
                    )

                searches += 1
                for provider in self.providers:
                    try:
                        quotes = provider.search(query, self.config.scanner.max_results_per_search)
                    except Exception as exc:
                        failures_count += 1
                        LOGGER.warning("Search failed for %s->%s: %s", origin, destination, exc)
                        continue

                    quotes_count += len(quotes)
                    for quote in quotes:
                        baseline = self.store.baseline_for(quote, self.config.detection.history_days)
                        route_threshold = RouteThreshold(
                            origin=origin,
                            destination=destination,
                        )
                        alert = self.detector.evaluate(quote, route_threshold, baseline)
                        fare_id = self.store.save_quote(quote, alert=alert, baseline=baseline)

                        if alert:
                            alerts_count += 1
                            enqueued_count += self._enqueue_for_subscribers(
                                alert, fare_id
                            )

                    if self.config.scanner.request_delay_seconds > 0:
                        time.sleep(self.config.scanner.request_delay_seconds)

        return ScanStats(
            searches=searches,
            quotes=quotes_count,
            alerts=alerts_count,
            failures=failures_count,
            enqueued=enqueued_count,
        )

    def _queries_for_route(self, origin: str, destination: str) -> list[SearchQuery]:
        """Generate queries for a route based on subscriber dates."""
        today = date.today()
        queries: list[SearchQuery] = []

        for offset in range(
            self.config.scanner.days_ahead_start,
            self.config.scanner.days_ahead_end + 1,
        ):
            departure = today + timedelta(days=offset)
            for stay_length in self.config.scanner.stay_lengths:
                queries.append(
                    SearchQuery(
                        origin=origin,
                        destination=destination,
                        departure_date=departure,
                        return_date=departure + timedelta(days=stay_length),
                        adults=self.config.scanner.adults,
                        currency=self.config.scanner.currency,
                    )
                )
        return queries

    def _enqueue_for_subscribers(self, alert: DealAlert, fare_id: int) -> int:
        """Enqueue notifications for all subscribers matching this alert."""
        quote = alert.quote
        matching = self.user_store.subscriptions_matching(
            origin=quote.origin,
            destination=quote.destination,
            departure_date=quote.departure_date,
        )
        if not matching:
            return 0

        fingerprint = alert_fingerprint(alert)
        enqueued = 0
        for subscription in matching:
            subscriber = self.user_store.get_subscriber_by_id(subscription.subscriber_id)
            if not subscriber:
                continue

            # Check max_price constraint
            if subscription.max_price is not None and quote.price > subscription.max_price:
                continue

            # Determine priority based on tier
            if subscriber.tier == SubscriberTier.PREMIUM:
                priority = self.config.freemium.premium_priority
            else:
                # Free tier: only enqueue if discount is significant enough
                if alert.discount_ratio is None or alert.discount_ratio < self.config.freemium.free_tier_min_discount:
                    continue
                priority = self.config.freemium.free_priority

            if self.user_store.enqueue_notification(
                subscriber_id=subscriber.id,
                alert_fingerprint=fingerprint,
                fare_id=fare_id,
                priority=priority,
            ):
                enqueued += 1

        return enqueued