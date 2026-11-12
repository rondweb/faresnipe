"""Alembic environment configuration for faresnipe-multiuser."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get database URL from env var or config
database_url = os.environ.get(
    "FARESNIPE_MULTI_DATABASE_URL",
    "sqlite:///data/faresnipe_multiuser.sqlite3",
)

# Resolve relative SQLite paths against the project directory
if database_url.startswith("sqlite:///"):
    db_path = database_url[len("sqlite:///") :]
    if not os.path.isabs(db_path):
        project_dir = Path(__file__).resolve().parents[1]
        db_path = str(project_dir / db_path)
        database_url = f"sqlite:///{db_path}"
    # Ensure parent directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(database_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()