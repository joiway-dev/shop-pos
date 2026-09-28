"""initial: users, audit_logs, shop_settings

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        *_timestamps(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("pin_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'cashier')", name="ck_users_role"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "audit_logs",
        *_timestamps(),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    shop_settings = op.create_table(
        "shop_settings",
        *_timestamps(),
        sa.Column("shop_name", sa.String(200), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("phone", sa.String(50), nullable=False),
        sa.Column("logo_filename", sa.String(100), nullable=True),
        sa.Column("vat_mode", sa.String(10), nullable=False),
        sa.Column("tax_id", sa.String(13), nullable=True),
        sa.Column("branch_no", sa.String(5), nullable=True),
        sa.Column("vat_rate_bp", sa.Integer(), nullable=False),
        sa.Column("price_includes_vat", sa.Boolean(), nullable=False),
        sa.Column("allow_abbreviated_invoice", sa.Boolean(), nullable=False),
        sa.Column("backup_dir", sa.String(500), nullable=True),
        sa.Column("backup_keep_days", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_shop_settings_single_row"),
        sa.CheckConstraint("vat_mode IN ('none', 'vat')", name="ck_shop_settings_vat_mode"),
    )
    op.execute(
        shop_settings.insert().values(
            id=1,
            created_at=sa.func.current_timestamp(),
            updated_at=sa.func.current_timestamp(),
            shop_name="",
            address="",
            phone="",
            vat_mode="none",
            branch_no="00000",
            vat_rate_bp=700,
            price_includes_vat=True,
            allow_abbreviated_invoice=False,
            backup_keep_days=30,
        )
    )


def downgrade() -> None:
    op.drop_table("shop_settings")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("users")
