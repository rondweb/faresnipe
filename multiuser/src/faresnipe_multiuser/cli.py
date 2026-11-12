"""CLI entry point for faresnipe-multiuser."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_config
from .db import Database
from .delivery import DeliveryProcessor
from .scanner import FlightScanner
from .telegram_bot import TelegramBot
from .user_store import UserStore

DEFAULT_CONFIG = Path("config/multiuser.toml")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="faresnipe-multiuser",
        description="Multi-user Telegram flight fare monitoring with freemium delivery.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to the multiuser TOML config.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # bot command
    bot_p = sub.add_parser("bot", help="Run the Telegram bot (long-polling).")
    bot_p.add_argument("--cycles", type=int, default=None, help="Run N polling cycles (default: forever).")

    # run command
    run_p = sub.add_parser("run", help="Run a scan. Use --watch for continuous.")
    run_p.add_argument("--watch", action="store_true", help="Run continuously on a schedule.")
    run_p.add_argument("--once", action="store_true", help="Run one scan and exit.")
    run_p.add_argument("--limit", type=int, default=None, help="Limit the number of searches.")
    run_p.add_argument(
        "--provider",
        choices=["google_flights_structured", "mock"],
        help="Provider to use for this scan.",
    )

    # deliver command
    del_p = sub.add_parser("deliver", help="Process the notification delivery queue.")
    del_p.add_argument("--once", action="store_true", help="Process one batch and exit.")
    del_p.add_argument("--cycles", type=int, default=None, help="Run N delivery cycles (default: forever).")

    # migrate command
    sub.add_parser("migrate", help="Run Alembic migrations.")

    args = parser.parse_args(argv)

    if args.command == "migrate":
        _run_migrations()
        return

    config_path = Path(args.config)
    load_dotenv(config_path.resolve().parents[1] / ".env")
    config = load_config(config_path)

    db = Database(config.database.url)
    user_store = UserStore(db)

    if args.command == "bot":
        bot = TelegramBot(config=config, user_store=user_store)
        bot.run(cycles=args.cycles)
        return

    if args.command == "run":
        provider_names = [args.provider] if args.provider else None
        scanner = FlightScanner(
            config=config,
            db=db,
            user_store=user_store,
            provider_names=provider_names,
        )
        if args.watch:
            _run_watch_loop(scanner, config)
        else:
            stats = scanner.run_once(limit_searches=args.limit)
            print(
                f"scan complete: searches={stats.searches} quotes={stats.quotes} "
                f"alerts={stats.alerts} failures={stats.failures} enqueued={stats.enqueued}"
            )
        return

    if args.command == "deliver":
        processor = DeliveryProcessor(config=config, user_store=user_store)
        if args.once:
            sent = processor.run_once()
            print(f"delivery complete: sent={sent}")
        else:
            processor.run(cycles=args.cycles)
        return


def _run_migrations() -> None:
    """Run Alembic migrations."""
    import subprocess

    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def _run_watch_loop(scanner: FlightScanner, config) -> None:
    """Run the scanner in a watch loop."""
    import random
    import time

    while True:
        try:
            stats = scanner.run_once()
            print(
                f"watch cycle complete: searches={stats.searches} quotes={stats.quotes} "
                f"alerts={stats.alerts} failures={stats.failures} enqueued={stats.enqueued}"
            )
        except Exception as exc:
            print(f"watch cycle failed: {exc}")

        interval = config.scanner.scan_interval_minutes * 60
        jitter = random.uniform(-config.scanner.scan_jitter_seconds, config.scanner.scan_jitter_seconds)
        sleep_seconds = max(0, interval + jitter)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main(sys.argv[1:])