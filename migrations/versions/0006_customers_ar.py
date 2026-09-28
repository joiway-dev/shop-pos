"""customers, quotations, receivables; sales.customer_id FK + due_date

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
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
        "customers",
        *_timestamps(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(50), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(13), nullable=True),
        sa.Column("branch", sa.String(5), nullable=True),
        sa.Column("credit_limit", sa.Integer(), nullable=False),
        sa.Column("credit_days", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_customers_name", "customers", ["name"])

    op.create_table(
        "quotations",
        *_timestamps(),
        sa.Column("doc_no", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("bill_discount_text", sa.String(20), nullable=False),
        sa.Column("subtotal", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("vatable_amount", sa.Integer(), nullable=False),
        sa.Column("exempt_amount", sa.Integer(), nullable=False),
        sa.Column("vat_amount", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("vat_rate_snapshot", sa.Integer(), nullable=False),
        sa.Column("shop_snapshot", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("converted_sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("doc_no"),
        sa.CheckConstraint("status IN ('draft', 'sent', 'converted')", name="ck_quotations_status"),
    )
    op.create_index("ix_quotations_customer_id", "quotations", ["customer_id"])

    op.create_table(
        "quotation_lines",
        *_timestamps(),
        sa.Column("quotation_id", sa.Integer(), sa.ForeignKey("quotations.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("product_units.id"), nullable=True),
        sa.Column("sku_snapshot", sa.String(50), nullable=False),
        sa.Column("product_name_snapshot", sa.String(200), nullable=False),
        sa.Column("unit_name", sa.String(30), nullable=False),
        sa.Column("factor_to_base", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("discount_text", sa.String(20), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Integer(), nullable=False),
        sa.Column("vat_exempt", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_quotation_lines_quotation_id", "quotation_lines", ["quotation_id"])

    op.create_table(
        "ar_payments",
        *_timestamps(),
        sa.Column("doc_no", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("shop_snapshot", sa.JSON(), nullable=False),
        sa.Column("customer_snapshot", sa.JSON(), nullable=False),
        sa.Column("void_reason", sa.String(500), nullable=True),
        sa.Column("voided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("print_count", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("doc_no"),
        sa.CheckConstraint("status IN ('completed', 'voided')", name="ck_ar_payments_status"),
        sa.CheckConstraint("method IN ('cash', 'transfer', 'cheque')", name="ck_ar_payments_method"),
    )
    op.create_index("ix_ar_payments_customer_id", "ar_payments", ["customer_id"])

    op.create_table(
        "ar_allocations",
        *_timestamps(),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("ar_payments.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_ar_allocations_amount"),
    )
    op.create_index("ix_ar_allocations_payment_id", "ar_allocations", ["payment_id"])
    op.create_index("ix_ar_allocations_sale_id", "ar_allocations", ["sale_id"])

    with op.batch_alter_table("sales") as batch:
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch.create_foreign_key("fk_sales_customer_id", "customers", ["customer_id"], ["id"])
        batch.create_index("ix_sales_customer_id", ["customer_id"])


def downgrade() -> None:
    with op.batch_alter_table("sales") as batch:
        batch.drop_index("ix_sales_customer_id")
        batch.drop_constraint("fk_sales_customer_id", type_="foreignkey")
        batch.drop_column("due_date")
    op.drop_table("ar_allocations")
    op.drop_table("ar_payments")
    op.drop_table("quotation_lines")
    op.drop_table("quotations")
    op.drop_table("customers")
