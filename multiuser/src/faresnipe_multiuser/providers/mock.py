"""Mock flight provider for testing and development."""

from __future__ import annotations

from decimal import Decimal

from ..models import FareQuote, SearchQuery


class MockProvider:
    """Deterministic mock provider that returns fake fares."""

    name = "mock"

    def __init__(self, base_price: Decimal | None = None) -> None:
        self.base_price = base_price

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        """Return deterministic fake quotes for the given query."""
        price = self.base_price or Decimal("250.00")
        # Vary price slightly by destination hash for determinism
        seed = sum(ord(c) for c in query.destination) % 100
        price = price + Decimal(seed)

        quotes: list[FareQuote] = []
        for i in range(min(max_results, 3)):
            quotes.append(
                FareQuote(
                    provider=self.name,
                    origin=query.origin,
                    destination=query.destination,
                    departure_date=query.departure_date,
                    return_date=query.return_date,
                    price=price + Decimal(i * 10),
                    currency=query.currency,
                    carrier="MockAir",
                    flight_numbers=(f"MA{i + 1:03d}",),
                    stops=0,
                    duration="2h 30m",
                    departure_time="08:00",
                    arrival_time="10:30",
                    booking_url=f"https://example.com/booking?origin={query.origin}&dest={query.destination}&date={query.departure_date.isoformat()}",
                )
            )
        return quotes


def build_mock_provider() -> MockProvider:
    return MockProvider()