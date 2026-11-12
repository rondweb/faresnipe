# faresnipe-multiuser

Multi-user Telegram flight fare monitoring service with freemium delivery queue.

## Features

- **Multi-user via Telegram**: Users register via bot, subscribe to routes (origin → destination + date)
- **Group support**: Bot works in private chats and Telegram groups
- **Freemium model**: Premium users get priority delivery + unlimited notifications; free users get capped daily notifications
- **PostgreSQL + SQLite**: Database abstraction supports both backends
- **Alembic migrations**: Versioned schema migrations
- **Priority delivery queue**: Premium subscribers get notifications first

## Quick start

```bash
# Setup
cd multiuser
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your FARESNIPE_MULTI_TELEGRAM_BOT_TOKEN and
# FARESNIPE_MULTI_DATABASE_URL

# Run migrations
uv run faresnipe-multiuser migrate

# Run (3 terminals)
uv run faresnipe-multiuser bot          # Telegram bot
uv run faresnipe-multiuser run --watch   # Scanner
uv run faresnipe-multiuser deliver       # Delivery queue
```

## Configuration

See `config/multiuser.toml` and `.env.example` for all options.

The CLI loads `.env` from the `multiuser` directory. The multi-user bot reads
`FARESNIPE_MULTI_TELEGRAM_BOT_TOKEN`; the older
`FARESNIPE_TELEGRAM_BOT_TOKEN` name is also accepted when both services share
the same `.env` file.