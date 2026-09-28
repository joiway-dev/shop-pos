"""Products, units and aliases.

Money columns are satang (INTEGER); quantity/factor columns are qty x 1000.
"""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

QTY_SCALE = 1000  # stored quantity = real quantity x 1000

ALIAS_SOURCE_MANUAL = "manual"
ALIAS_SOURCE_LEARNED = "learned"


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    base_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    avg_cost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # satang / base unit
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_stock_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # x1000

    category: Mapped[Category | None] = relationship()
    units: Mapped[list["ProductUnit"]] = relationship(
        back_populates="product", order_by="ProductUnit.factor_to_base", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["ProductAlias"]] = relationship(
        back_populates="product", order_by="ProductAlias.alias_text", cascade="all, delete-orphan"
    )

    @property
    def base_unit_row(self) -> "ProductUnit | None":
        return next((u for u in self.units if u.unit_name == self.base_unit), None)

    @property
    def default_unit(self) -> "ProductUnit | None":
        return next((u for u in self.units if u.is_default_sale_unit), self.base_unit_row)


class ProductUnit(TimestampMixin, Base):
    __tablename__ = "product_units"
    __table_args__ = (
        UniqueConstraint("product_id", "unit_name", name="uq_product_units_name"),
        CheckConstraint("factor_to_base > 0", name="ck_product_units_factor"),
        CheckConstraint("price >= 0", name="ck_product_units_price"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    unit_name: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_to_base: Mapped[int] = mapped_column(Integer, nullable=False)  # x1000
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # satang
    is_default_sale_unit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(50), index=True)

    product: Mapped[Product] = relationship(back_populates="units")


class ProductAlias(TimestampMixin, Base):
    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("product_id", "alias_normalized", name="uq_product_aliases_norm"),
        CheckConstraint("source IN ('manual', 'learned')", name="ck_product_aliases_source"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    alias_text: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    product: Mapped[Product] = relationship(back_populates="aliases")
