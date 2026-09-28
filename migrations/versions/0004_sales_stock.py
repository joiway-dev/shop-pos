"""sales, sale_lines, doc_sequences, stock_movements, stock_balances; doc number reset setting

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
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
        "stock_movements",
        *_timestamps(),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("qty_base", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("ref_type", sa.String(20), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("unit_cost", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "type IN ('sale', 'sale_void', 'purchase', 'adjust', 'return')", name="ck_stock_movements_type"
        ),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])

    op.create_table(
        "stock_balances",
        *_timestamps(),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("qty_base", sa.Integer(), nullable=False),
        sa.UniqueConstraint("product_id"),
    )

    op.create_table(
        "doc_sequences",
        *_timestamps(),
        sa.Column("doc_type", sa.String(10), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("last_no", sa.Integer(), nullable=False),
        sa.UniqueConstraint("doc_type", "period", name="uq_doc_sequences_type_period"),
    )

    op.create_table(
        "sales",
        *_timestamps(),
        sa.Column("doc_type", sa.String(10), nullable=False),
        sa.Column("doc_no", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("subtotal", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("vatable_amount", sa.Integer(), nullable=False),
        sa.Column("exempt_amount", sa.Integer(), nullable=False),
        sa.Column("vat_amount", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("payment_type", sa.String(10), nullable=False),
        sa.Column("cash_received", sa.Integer(), nullable=True),
        sa.Column("change_amount", sa.Integer(), nullable=True),
        sa.Column("shop_snapshot", sa.JSON(), nullable=False),
        sa.Column("buyer_snapshot", sa.JSON(), nullable=True),
        sa.Column("vat_rate_snapshot", sa.Integer(), nullable=False),
        sa.Column("void_reason", sa.String(500), nullable=True),
        sa.Column("voided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("replaced_by_sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("print_count", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("doc_no"),
        sa.CheckConstraint("status IN ('completed', 'voided')", name="ck_sales_status"),
        sa.CheckConstraint("payment_type IN ('cash', 'transfer', 'credit')", name="ck_sales_payment_type"),
    )
    op.create_index("ix_sales_user_id", "sales", ["user_id"])

    op.create_table(
        "sale_lines",
        *_timestamps(),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku_snapshot", sa.String(50), nullable=False),
        sa.Column("product_name_snapshot", sa.String(200), nullable=False),
        sa.Column("unit_name", sa.String(30), nullable=False),
        sa.Column("factor_to_base", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Integer(), nullable=False),
        sa.Column("vat_exempt", sa.Boolean(), nullable=False),
        sa.Column("unit_cost_snapshot", sa.Integer(), nullable=False),
    )
    op.create_index("ix_sale_lines_sale_id", "sale_lines", ["sale_id"])
    op.create_index("ix_sale_lines_product_id", "sale_lines", ["product_id"])

    with op.batch_alter_table("shop_settings") as batch:
        batch.add_column(sa.Column("doc_number_reset", sa.String(10), server_default="monthly", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("shop_settings") as batch:
        batch.drop_column("doc_number_reset")
    op.drop_table("sale_lines")
    op.drop_table("sales")
    op.drop_table("doc_sequences")
    op.drop_table("stock_balances")
    op.drop_table("stock_movements")
