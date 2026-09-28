"""daily_closes; shop_settings.went_live_at

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_closes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("opening_cash", sa.Integer(), nullable=False),
        sa.Column("cash_sales", sa.Integer(), nullable=False),
        sa.Column("cash_receipts", sa.Integer(), nullable=False),
        sa.Column("expected_cash", sa.Integer(), nullable=False),
        sa.Column("counted_cash", sa.Integer(), nullable=False),
        sa.Column("difference", sa.Integer(), nullable=False),
        sa.Column("transfer_total", sa.Integer(), nullable=False),
        sa.Column("credit_total", sa.Integer(), nullable=False),
        sa.Column("bill_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_daily_closes_business_date", "daily_closes", ["business_date"])
    with op.batch_alter_table("shop_settings") as batch:
        batch.add_column(sa.Column("went_live_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shop_settings") as batch:
        batch.drop_column("went_live_at")
    op.drop_table("daily_closes")
