"""purchasing: suppliers, purchases, purchase_lines; user permissions; document prefixes

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
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
        "suppliers",
        *_timestamps(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(50), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(13), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "purchases",
        *_timestamps(),
        sa.Column("doc_no", sa.String(30), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("supplier_invoice_no", sa.String(50), nullable=False),
        sa.Column("supplier_invoice_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("vat_type", sa.String(10), nullable=False),
        sa.Column("vat_rate_bp", sa.Integer(), nullable=False),
        sa.Column("subtotal", sa.Integer(), nullable=False),
        sa.Column("vat_amount", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("costed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("void_reason", sa.String(500), nullable=True),
        sa.Column("voided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("doc_no"),
        sa.CheckConstraint("status IN ('pending_cost', 'completed', 'voided')", name="ck_purchases_status"),
        sa.CheckConstraint("vat_type IN ('none', 'included', 'excluded')", name="ck_purchases_vat_type"),
    )
    op.create_index("ix_purchases_supplier_id", "purchases", ["supplier_id"])

    op.create_table(
        "purchase_lines",
        *_timestamps(),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name_snapshot", sa.String(200), nullable=False),
        sa.Column("unit_name", sa.String(30), nullable=False),
        sa.Column("factor_to_base", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Integer(), nullable=True),
        sa.Column("line_total", sa.Integer(), nullable=True),
        sa.Column("cost_base", sa.Integer(), nullable=True),
    )
    op.create_index("ix_purchase_lines_purchase_id", "purchase_lines", ["purchase_id"])
    op.create_index("ix_purchase_lines_product_id", "purchase_lines", ["product_id"])

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("can_receive_stock", sa.Boolean(), server_default="0", nullable=False))
        batch.add_column(sa.Column("can_see_cost", sa.Boolean(), server_default="0", nullable=False))

    with op.batch_alter_table("shop_settings") as batch:
        batch.add_column(sa.Column("doc_prefixes", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shop_settings") as batch:
        batch.drop_column("doc_prefixes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("can_see_cost")
        batch.drop_column("can_receive_stock")
    op.drop_table("purchase_lines")
    op.drop_table("purchases")
    op.drop_table("suppliers")
