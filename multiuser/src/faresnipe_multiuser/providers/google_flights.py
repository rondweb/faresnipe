"""Google Flights provider using the fast-flights library."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote_plus

from ..models import FareQuote, SearchQuery


class GoogleFlightsProvider:
    """Provider that queries Google Flights via the fast-flights library."""

    name = "google_flights_structured"

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        try:
            from fast_flights import FlightQuery, Passengers, create_query, get_flights
        except ImportError as exc:
            raise RuntimeError(
                "fast-flights is required for provider='google_flights_structured'. "
                "Install dependencies with: uv sync"
            ) from exc

        flights = [
            FlightQuery(
                date=query.departure_date.isoformat(),
                from_airport=query.origin,
                to_airport=query.destination,
            )
        ]
        trip = "one-way"
        if query.return_date is not None:
            flights.append(
                FlightQuery(
                    date=query.return_date.isoformat(),
                    from_airport=query.destination,
                    to_airport=query.origin,
                )
            )
            trip = "round-trip"

        fast_query = create_query(
            flights=flights,
            trip=trip,
            passengers=Passengers(adults=query.adults),
            currency=query.currency,
        )
        try:
            result = get_flights(fast_query)
        except Exception as exc:
            raise RuntimeError(f"Google Flights search failed: {exc}") from exc

        quotes: list[FareQuote] = []
        for flight in list(result)[:max_results]:
            segments = list(getattr(flight, "flights", []) or [])
            if not segments or getattr(flight, "price", None) is None:
                continue
            first = segments[0]
            last = segments[-1]
            quotes.append(
                FareQuote(
                    provider=self.name,
                    origin=query.origin,
                    destination=query.destination,
                    departure_date=query.departure_date,
                    return_date=query.return_date,
                    price=Decimal(str(flight.price)),
                    currency=query.currency,
                    carrier=", ".join(str(value) for value in (getattr(flight, "airlines", []) or []))
                    or str(getattr(flight, "type", "") or "")
                    or None,
                    flight_numbers=tuple(
                        f"{getattr(segment, 'airline', '')}{getattr(segment, 'flight_number', '')}"
                        for segment in segments
                    ),
                    stops=max(0, len(segments) - 1),
                    duration=_format_duration(
                        sum(int(getattr(segment, "duration", 0) or 0) for segment in segments)
                    ),
                    departure_time=_format_time(
                        getattr(getattr(first, "departure", None), "time", None)
                    ),
                    arrival_time=_format_time(
                        getattr(getattr(last, "arrival", None), "time", None)
                    ),
                    booking_url=_booking_url(query),
                )
            )
        return quotes


def build_google_flights_provider() -> GoogleFlightsProvider:
    return GoogleFlightsProvider()


def _format_duration(minutes: int) -> str | None:
    if minutes <= 0:
        return None
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} hr {mins} min"
    if hours:
        return f"{hours} hr"
    return f"{mins} min"


def _format_time(value: object) -> str | None:
    if not value:
        return None
    parts = list(value)
    if len(parts) < 2 or parts[1] is None:
        return None
    return f"{int(parts[0] or 0):02d}:{int(parts[1]):02d}"


def _booking_url(query: SearchQuery) -> str:
    search = f"Flights from {query.origin} to {query.destination} on {query.departure_date.isoformat()}"
    if query.return_date:
        search += f" through {query.return_date.isoformat()}"
    return f"https://www.google.com/travel/flights?q={quote_plus(search)}"
