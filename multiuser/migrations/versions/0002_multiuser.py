"""Alembic migration: add multi-user tables (subscribers, subscriptions, queue)."""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create multi-user tables."""
    op.create_table(
        "subscribers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("chat_type", sa.String(), nullable=False, server_default="private"),
        sa.Column("chat_title", sa.String(), nullable=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), nullable=False, server_default="free"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscriber_id", sa.BigInteger(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("departure_date", sa.String(), nullable=False),
        sa.Column("departure_flex_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("return_date", sa.String(), nullable=True),
        sa.Column("max_price", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"]),
    )

    op.create_table(
        "notification_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscriber_id", sa.BigInteger(), nullable=False),
        sa.Column("alert_fingerprint", sa.String(), nullable=False),
        sa.Column("fare_id", sa.BigInteger(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("sent_at", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.UniqueConstraint("subscriber_id", "alert_fingerprint"),
        sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"]),
    )

    op.create_table(
        "conversation_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    """Drop multi-user tables."""
    op.drop_table("conversation_state")
    op.drop_table("notification_queue")
    op.drop_table("subscriptions")
    op.drop_table("subscribers")
