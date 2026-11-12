"""Fix SQLite auto-increment types for multi-user tables."""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make SQLite primary keys INTEGER PRIMARY KEY for auto-increment."""
    if op.get_bind().dialect.name != "sqlite":
        return

    for table in (
        "subscribers",
        "subscriptions",
        "notification_queue",
        "conversation_state",
    ):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column("id", existing_type=sa.BigInteger(), type_=sa.Integer())


def downgrade() -> None:
    """Restore the previous SQLite integer type declaration."""
    if op.get_bind().dialect.name != "sqlite":
        return

    for table in (
        "conversation_state",
        "notification_queue",
        "subscriptions",
        "subscribers",
    ):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column("id", existing_type=sa.Integer(), type_=sa.BigInteger())
