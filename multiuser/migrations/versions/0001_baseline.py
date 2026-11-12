"""Alembic baseline schema."""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.create_table("fares", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("provider", sa.String(), nullable=False), sa.Column("origin", sa.String(), nullable=False), sa.Column("destination", sa.String(), nullable=False), sa.Column("departure_date", sa.String(), nullable=False), sa.Column("return_date", sa.String(), nullable=True), sa.Column("trip_kind", sa.String(), nullable=False), sa.Column("price", sa.String(), nullable=False), sa.Column("currency", sa.String(), nullable=False), sa.Column("carrier", sa.String(), nullable=True), sa.Column("flight_numbers", sa.String(), nullable=True), sa.Column("stops", sa.Integer(), nullable=True), sa.Column("duration", sa.String(), nullable=True), sa.Column("departure_time", sa.String(), nullable=True), sa.Column("arrival_time", sa.String(), nullable=True), sa.Column("booking_url", sa.String(), nullable=True), sa.Column("observed_at", sa.String(), nullable=False), sa.Column("severity", sa.String(), nullable=True), sa.Column("baseline_price", sa.String(), nullable=True))
    op.create_table("alerts", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("fingerprint", sa.String(), nullable=False), sa.Column("created_at", sa.String(), nullable=False))
    op.create_table("scan_runs", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("provider", sa.String(), nullable=False), sa.Column("status", sa.String(), nullable=False), sa.Column("limit_searches", sa.Integer(), nullable=True), sa.Column("total_searches", sa.Integer(), nullable=True), sa.Column("searches", sa.Integer(), nullable=False, server_default="0"), sa.Column("quotes", sa.Integer(), nullable=False, server_default="0"), sa.Column("alerts", sa.Integer(), nullable=False, server_default="0"), sa.Column("failures", sa.Integer(), nullable=False, server_default="0"), sa.Column("current_origin", sa.String(), nullable=True), sa.Column("current_destination", sa.String(), nullable=True), sa.Column("current_departure_date", sa.String(), nullable=True), sa.Column("current_return_date", sa.String(), nullable=True), sa.Column("error", sa.String(), nullable=True), sa.Column("started_at", sa.String(), nullable=False), sa.Column("updated_at", sa.String(), nullable=False), sa.Column("completed_at", sa.String(), nullable=True))
    op.create_table("scan_failures", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("scan_run_id", sa.Integer(), nullable=False), sa.Column("origin", sa.String(), nullable=False), sa.Column("destination", sa.String(), nullable=False), sa.Column("departure_date", sa.String(), nullable=False), sa.Column("return_date", sa.String(), nullable=True), sa.Column("provider", sa.String(), nullable=True), sa.Column("error", sa.String(), nullable=False), sa.Column("created_at", sa.String(), nullable=False))
def downgrade() -> None:
    op.drop_table("scan_failures")
    op.drop_table("scan_runs")
    op.drop_table("alerts")
    op.drop_table("fares")
