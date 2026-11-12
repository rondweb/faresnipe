"""Flight providers for faresnipe-multiuser."""

from __future__ import annotations

from collections.abc import Callable

from .base import FlightProvider
from .google_flights import GoogleFlightsProvider, build_google_flights_provider
from .mock import MockProvider, build_mock_provider

PROVIDER_BUILDERS: dict[str, Callable[[], FlightProvider]] = {
    "mock": build_mock_provider,
    "google_flights_structured": build_google_flights_provider,
    "google_flights": build_google_flights_provider,
    "fast_flights": build_google_flights_provider,
}


def build_provider(name: str) -> FlightProvider:
    key = name.strip().lower()
    builder = PROVIDER_BUILDERS.get(key)
    if builder is None:
        raise ValueError(f"Unknown provider: {name}")
    return builder()


def build_providers(names: list[str] | tuple[str, ...]) -> tuple[FlightProvider, ...]:
    return tuple(build_provider(name) for name in names)


__all__ = [
    "FlightProvider",
    "GoogleFlightsProvider",
    "MockProvider",
    "build_provider",
    "build_providers",
]