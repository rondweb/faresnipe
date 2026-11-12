"""Tests for the Google Flights provider."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from faresnipe_multiuser.models import SearchQuery
from faresnipe_multiuser.providers.google_flights import GoogleFlightsProvider


def test_search_uses_current_fast_flights_query_api() -> None:
    query = SearchQuery("YYZ", "GRU", date(2026, 12, 1), date(2026, 12, 8), 1, "USD")

    with patch("fast_flights.get_flights", return_value=[]) as get_flights:
        quotes = GoogleFlightsProvider().search(query, max_results=8)

    assert quotes == []
    fast_query = get_flights.call_args.args[0]
    assert fast_query.trip == 1
    assert [flight.from_airport.airport for flight in fast_query.flight_data] == ["YYZ", "GRU"]
