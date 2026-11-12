"""Tests for multiuser configuration loading."""

from __future__ import annotations

from pathlib import Path

from faresnipe_multiuser.config import load_config


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "multiuser.toml"
    config_path.write_text(
        """
[database]
url = "sqlite:///data/test.sqlite3"

[telegram]
bot_token = ""
""",
        encoding="utf-8",
    )
    return config_path


def test_load_config_accepts_legacy_telegram_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FARESNIPE_MULTI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("FARESNIPE_TELEGRAM_BOT_TOKEN", "legacy-token")

    config = load_config(_write_config(tmp_path))

    assert config.telegram.bot_token == "legacy-token"


def test_load_config_prefers_multiuser_telegram_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FARESNIPE_TELEGRAM_BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("FARESNIPE_MULTI_TELEGRAM_BOT_TOKEN", "multiuser-token")

    config = load_config(_write_config(tmp_path))

    assert config.telegram.bot_token == "multiuser-token"
