"""catalog: categories, products, product_units, product_aliases; matching settings

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
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
        "categories",
        *_timestamps(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "products",
        *_timestamps(),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("base_unit", sa.String(30), nullable=False),
        sa.Column("avg_cost", sa.Integer(), nullable=False),
        sa.Column("vat_exempt", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("min_stock_qty", sa.Integer(), nullable=False),
        sa.UniqueConstraint("sku"),
    )
    op.create_index("ix_products_barcode", "products", ["barcode"])
    op.create_index("ix_products_name_normalized", "products", ["name_normalized"])
    op.create_index("ix_products_category_id", "products", ["category_id"])

    op.create_table(
        "product_units",
        *_timestamps(),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("unit_name", sa.String(30), nullable=False),
        sa.Column("factor_to_base", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("is_default_sale_unit", sa.Boolean(), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=True),
        sa.UniqueConstraint("product_id", "unit_name", name="uq_product_units_name"),
        sa.CheckConstraint("factor_to_base > 0", name="ck_product_units_factor"),
        sa.CheckConstraint("price >= 0", name="ck_product_units_price"),
    )
    op.create_index("ix_product_units_product_id", "product_units", ["product_id"])
    op.create_index("ix_product_units_barcode", "product_units", ["barcode"])

    op.create_table(
        "product_aliases",
        *_timestamps(),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("alias_text", sa.String(200), nullable=False),
        sa.Column("alias_normalized", sa.String(200), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("product_id", "alias_normalized", name="uq_product_aliases_norm"),
        sa.CheckConstraint("source IN ('manual', 'learned')", name="ck_product_aliases_source"),
    )
    op.create_index("ix_product_aliases_product_id", "product_aliases", ["product_id"])
    op.create_index("ix_product_aliases_alias_normalized", "product_aliases", ["alias_normalized"])

    with op.batch_alter_table("shop_settings") as batch:
        batch.add_column(sa.Column("match_auto_accept", sa.Integer(), server_default="92", nullable=False))
        batch.add_column(sa.Column("match_suggest_min", sa.Integer(), server_default="60", nullable=False))
        batch.add_column(sa.Column("match_min_gap", sa.Integer(), server_default="8", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("shop_settings") as batch:
        batch.drop_column("match_min_gap")
        batch.drop_column("match_suggest_min")
        batch.drop_column("match_auto_accept")
    op.drop_table("product_aliases")
    op.drop_table("product_units")
    op.drop_table("products")
    op.drop_table("categories")
