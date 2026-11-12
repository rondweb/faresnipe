"""Fare storage layer using the unified Database abstraction."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .db import Database
from .models import Baseline, FareQuote, SearchQuery


class FareStore:
    """Stores fare quotes, alerts, and scan runs in the database."""

    DAYS_TO_DEPARTURE_TOLERANCE = 21

    def __init__(self, db: Database) -> None:
        self.db = db
        self._init_db()

    def save_quote(self, quote: FareQuote, alert: Any | None = None, baseline: Baseline | None = None) -> int:
        """Insert a fare quote and return its row id."""
        returning = " RETURNING id" if self.db.backend == "postgresql" else ""
        with self.db.connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO fares (
                    provider, origin, destination, departure_date, return_date, trip_kind,
                    price, currency, carrier, flight_numbers, stops, duration,
                    departure_time, arrival_time, booking_url, observed_at,
                    severity, baseline_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                {returning}
                """,
                (
                    quote.provider, quote.origin, quote.destination,
                    quote.departure_date.isoformat(),
                    quote.return_date.isoformat() if quote.return_date else None,
                    quote.trip_kind.value, str(quote.price), quote.currency, quote.carrier,
                    ",".join(quote.flight_numbers), quote.stops, quote.duration,
                    quote.departure_time, quote.arrival_time, quote.booking_url,
                    quote.observed_at.isoformat(timespec="seconds"),
                    getattr(alert, "severity", None) if alert else None,
                    str(baseline.median_price) if baseline and baseline.median_price is not None else None,
                ),
            )
            conn.commit()
            if self.db.backend == "postgresql":
                row = cur.fetchone()
                return int(row["id"]) if row else 0
            return int(conn.lastrowid)

    def baseline_for(self, quote: FareQuote, history_days: int) -> Baseline:
        prices = self._comparable_prices(quote, history_days)
        return _baseline(prices)

    def latest_alert_fingerprint_seen(self, fingerprint: str, hours: int) -> bool:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alerts WHERE fingerprint = ? AND created_at >= ? LIMIT 1",
                (fingerprint, since.isoformat(timespec="seconds")),
            ).fetchone()
        return row is not None

    def save_alert(self, fingerprint: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO alerts (fingerprint, created_at) VALUES (?, ?)",
                (fingerprint, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()

    def begin_scan_run(self, provider: str, limit_searches: int | None = None, total_searches: int | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        returning = " RETURNING id" if self.db.backend == "postgresql" else ""
        with self.db.connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO scan_runs (
                    provider, status, limit_searches, total_searches,
                    searches, quotes, alerts, failures, started_at, updated_at
                ) VALUES (?, 'running', ?, ?, 0, 0, 0, 0, ?, ?)
                {returning}
                """,
                (provider, limit_searches, total_searches, now, now),
            )
            conn.commit()
            if self.db.backend == "postgresql":
                row = cur.fetchone()
                return int(row["id"]) if row else 0
            return int(conn.lastrowid)

    def update_scan_run(self, run_id: int, **kwargs: Any) -> None:
        fields = ["status", "searches", "quotes", "alerts", "failures", "error"]
        sets = ["updated_at = ?"]
        values: list[Any] = [datetime.now(timezone.utc).isoformat(timespec="seconds")]
        for field in fields:
            if kwargs.get(field) is not None:
                sets.append(f"{field} = ?")
                values.append(kwargs[field])
        query = kwargs.get("current_query")
        if isinstance(query, SearchQuery):
            sets += [
                "current_origin = ?", "current_destination = ?",
                "current_departure_date = ?", "current_return_date = ?",
            ]
            values += [
                query.origin, query.destination, query.departure_date.isoformat(),
                query.return_date.isoformat() if query.return_date else None,
            ]
        elif kwargs.get("clear_current"):
            sets += [
                "current_origin = NULL", "current_destination = NULL",
                "current_departure_date = NULL", "current_return_date = NULL",
            ]
        if kwargs.get("complete"):
            sets.append("completed_at = ?")
            values.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        values.append(run_id)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE scan_runs SET {', '.join(sets)} WHERE id = ?", tuple(values))
            conn.commit()

    def save_scan_failure(self, run_id: int, query: SearchQuery, error: str, provider: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_failures (
                    scan_run_id, origin, destination, departure_date, return_date,
                    provider, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, query.origin, query.destination, query.departure_date.isoformat(),
                    query.return_date.isoformat() if query.return_date else None,
                    provider, error, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

    def scan_failures(self, run_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        where = "WHERE scan_run_id = ?" if run_id is not None else ""
        params: tuple[Any, ...] = (run_id, limit) if run_id is not None else (limit,)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, scan_run_id, origin, destination, departure_date,
                       return_date, provider, error, created_at
                FROM scan_failures {where}
                ORDER BY id DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_scan_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, provider, status, limit_searches, total_searches,
                       searches, quotes, alerts, failures, current_origin, current_destination,
                       current_departure_date, current_return_date, error,
                       started_at, updated_at, completed_at
                FROM scan_runs ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_scan_run(self) -> dict[str, Any] | None:
        rows = self.recent_scan_runs(1)
        return rows[0] if rows else None

    def mark_interrupted_scan_runs(self) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE scan_runs
                SET status = 'interrupted',
                    error = COALESCE(error, 'Process stopped before this scan completed.'),
                    current_origin = NULL, current_destination = NULL,
                    current_departure_date = NULL, current_return_date = NULL,
                    updated_at = ?, completed_at = COALESCE(completed_at, ?)
                WHERE status = 'running'
                """,
                (now, now),
            )
            conn.commit()

    def recent_quotes(self, limit: int = 20, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        return self._fare_rows(_fare_filters(currency, providers), "observed_at DESC, id DESC", limit)

    def cheapest_quotes(self, limit: int = 20, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        where, params = _fare_filters(currency, providers)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                  SELECT origin, destination, departure_date, return_date,
                         CAST(price AS NUMERIC) AS price, currency, provider, booking_url,
                         COUNT(*) OVER (
                           PARTITION BY origin, destination, departure_date, return_date, currency, provider
                         ) AS samples,
                         ROW_NUMBER() OVER (
                           PARTITION BY origin, destination, departure_date, return_date, currency, provider
                           ORDER BY CAST(price AS NUMERIC) ASC, observed_at DESC
                         ) AS rn
                  FROM fares {where}
                )
                SELECT origin, destination, departure_date, return_date, price,
                       currency, provider, booking_url, samples
                FROM ranked WHERE rn = 1
                ORDER BY price ASC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_by_route(self, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        where, params = _fare_filters(currency, providers)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                  SELECT {_FARE_COLUMNS},
                         ROW_NUMBER() OVER (
                           PARTITION BY origin, destination, departure_date, return_date, currency
                           ORDER BY CAST(price AS NUMERIC) ASC, observed_at DESC
                         ) rn
                  FROM fares {where}
                )
                SELECT {_FARE_COLUMNS} FROM ranked WHERE rn = 1 ORDER BY observed_at DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def route_stats(self, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        where, params = _fare_filters(currency, providers)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT origin, destination, currency, COUNT(*) AS samples,
                       MIN(CAST(price AS NUMERIC)) AS min_price,
                       AVG(CAST(price AS NUMERIC)) AS avg_price,
                       MAX(observed_at) AS last_seen
                FROM fares {where}
                GROUP BY origin, destination, currency
                ORDER BY origin, destination, currency
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def top_opportunities(self, limit: int = 20, min_discount: Decimal = Decimal("0.10"), min_history: int = 3, history_days: int = 180, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self.latest_by_route(currency=currency, providers=providers):
            quote = _row_to_quote(row)
            baseline = self.baseline_for(quote, history_days)
            if not baseline.median_price or baseline.quote_count < min_history:
                continue
            price = Decimal(str(row["price"]))
            discount = (baseline.median_price - price) / baseline.median_price
            if discount < min_discount:
                continue
            item = dict(row)
            item.update({
                "median_price": str(int(baseline.median_price)),
                "baseline_min_price": str(int(baseline.min_price)) if baseline.min_price else None,
                "baseline_count": baseline.quote_count,
                "discount_ratio": str(discount.quantize(Decimal("0.0001"))),
                "discount_pct": f"{(discount * Decimal('100')).quantize(Decimal('0.1'))}%",
            })
            out.append(item)
        out.sort(key=lambda r: (-Decimal(r["discount_ratio"]), Decimal(str(r["price"]))))
        return out[:limit]

    def price_history(self, origin: str, destination: str, limit: int = 120, currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        filter_sql, params = _fare_filters(currency, providers, prefix="AND")
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_FARE_COLUMNS} FROM fares
                WHERE origin = ? AND destination = ? {filter_sql}
                ORDER BY observed_at DESC, CAST(price AS NUMERIC) ASC LIMIT ?
                """,
                (origin.upper(), destination.upper(), *params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _fare_rows(self, filters: tuple[str, tuple[str, ...]], order: str, limit: int) -> list[dict[str, Any]]:
        where, params = filters
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT {_FARE_COLUMNS} FROM fares {where} ORDER BY {order} LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _comparable_prices(self, quote: FareQuote, history_days: int) -> list[Decimal]:
        since = datetime.now(timezone.utc) - timedelta(days=history_days)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT price, departure_date, return_date, observed_at FROM fares
                WHERE origin = ? AND destination = ? AND trip_kind = ? AND currency = ?
                  AND observed_at >= ?
                """,
                (quote.origin, quote.destination, quote.trip_kind.value, quote.currency, since.isoformat(timespec="seconds")),
            ).fetchall()
        return [
            Decimal(str(row["price"]))
            for row in rows
            if _is_comparable(quote, row)
        ]

    def _init_db(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS fares (
                    id {self.db.serial_pk},
                    provider TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL,
                    departure_date TEXT NOT NULL, return_date TEXT, trip_kind TEXT NOT NULL,
                    price TEXT NOT NULL, currency TEXT NOT NULL, carrier TEXT,
                    flight_numbers TEXT, stops INTEGER, duration TEXT,
                    departure_time TEXT, arrival_time TEXT, booking_url TEXT,
                    observed_at TEXT NOT NULL, severity TEXT, baseline_price TEXT
                )
                """
            )
            self.db.ensure_columns(conn, "fares", {"severity": "TEXT", "baseline_price": "TEXT", "stops": "INTEGER", "duration": "TEXT", "departure_time": "TEXT", "arrival_time": "TEXT"})
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS alerts (
                    id {self.db.serial_pk},
                    fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id {self.db.serial_pk}, provider TEXT NOT NULL, status TEXT NOT NULL,
                    limit_searches INTEGER, total_searches INTEGER, searches INTEGER NOT NULL DEFAULT 0,
                    quotes INTEGER NOT NULL DEFAULT 0, alerts INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, current_origin TEXT, current_destination TEXT,
                    current_departure_date TEXT, current_return_date TEXT, error TEXT,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                )
                """
            )
            self.db.ensure_columns(conn, "scan_runs", {"failures": "INTEGER NOT NULL DEFAULT 0"})
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS scan_failures (
                    id {self.db.serial_pk}, scan_run_id INTEGER NOT NULL,
                    origin TEXT NOT NULL, destination TEXT NOT NULL, departure_date TEXT NOT NULL,
                    return_date TEXT, provider TEXT, error TEXT NOT NULL, created_at TEXT NOT NULL
                )
                """
            )
            self.db.ensure_columns(conn, "scan_failures", {"provider": "TEXT"})
            conn.commit()


_FARE_COLUMNS = """
origin, destination, departure_date, return_date, price, currency, carrier,
stops, duration, departure_time, arrival_time, booking_url, provider, observed_at,
severity, baseline_price
""".strip()


def _baseline(prices: list[Decimal]) -> Baseline:
    if not prices:
        return Baseline(None, None, 0)
    prices.sort()
    mid = len(prices) // 2
    median = prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / Decimal("2")
    return Baseline(median, prices[0], len(prices))


def _is_comparable(quote: FareQuote, row: dict[str, Any]) -> bool:
    dep = date.fromisoformat(str(row["departure_date"]))
    ret = date.fromisoformat(str(row["return_date"])) if row["return_date"] else None
    obs = datetime.fromisoformat(str(row["observed_at"]))
    if dep.month != quote.departure_date.month:
        return False
    if _stay_length(dep, ret) != _stay_length(quote.departure_date, quote.return_date):
        return False
    return abs((quote.departure_date - quote.observed_at.date()).days - (dep - obs.date()).days) <= FareStore.DAYS_TO_DEPARTURE_TOLERANCE


def _stay_length(departure_date: date, return_date: date | None) -> int | None:
    return None if return_date is None else (return_date - departure_date).days


def _row_to_quote(row: dict[str, Any]) -> FareQuote:
    flights = tuple(part for part in str(row.get("flight_numbers") or "").split(",") if part)
    return FareQuote(
        provider=str(row["provider"]), origin=str(row["origin"]), destination=str(row["destination"]),
        departure_date=date.fromisoformat(str(row["departure_date"])),
        return_date=date.fromisoformat(str(row["return_date"])) if row.get("return_date") else None,
        price=Decimal(str(row["price"])), currency=str(row["currency"]),
        carrier=row.get("carrier"), flight_numbers=flights,
        stops=row.get("stops"), duration=row.get("duration"),
        departure_time=row.get("departure_time"), arrival_time=row.get("arrival_time"),
        booking_url=row.get("booking_url"),
        observed_at=datetime.fromisoformat(str(row["observed_at"])),
    )


def _fare_filters(currency: str | None = None, providers: tuple[str, ...] | list[str] | None = None, prefix: str = "WHERE") -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    if currency:
        clauses.append("currency = ?")
        params.append(currency.upper())
    provider_values = tuple(provider for provider in (providers or ()) if provider)
    if provider_values:
        clauses.append(f"provider IN ({', '.join('?' for _ in provider_values)})")
        params.extend(provider_values)
    return (f"{prefix} " + " AND ".join(clauses), tuple(params)) if clauses else ("", ())