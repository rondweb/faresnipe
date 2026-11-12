"""Provider interface for faresnipe-multiuser."""

from __future__ import annotations

from typing import Protocol

from ..models import FareQuote, SearchQuery


class FlightProvider(Protocol):
    name: str

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        ...