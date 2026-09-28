"""users: PIN lockout columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("failed_pin_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("locked_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("locked_until")
        batch.drop_column("failed_pin_count")
