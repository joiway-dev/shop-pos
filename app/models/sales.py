"""Stock movements, sales documents and document numbering.

Money = satang (INTEGER); quantities = x1000 (INTEGER).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

MOVE_SALE = "sale"
MOVE_SALE_VOID = "sale_void"
MOVE_PURCHASE = "purchase"
MOVE_ADJUST = "adjust"
MOVE_RETURN = "return"

SALE_COMPLETED = "completed"
SALE_VOIDED = "voided"

PAY_CASH = "cash"
PAY_TRANSFER = "transfer"
PAY_CREDIT = "credit"


class StockMovement(TimestampMixin, Base):
    """The only way stock changes (CLAUDE.md rule 5)."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "type IN ('sale', 'sale_void', 'purchase', 'adjust', 'return')", name="ck_stock_movements_type"
        ),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    qty_base: Mapped[int] = mapped_column(Integer, nullable=False)  # x1000, signed
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(20))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # satang / base unit
    note: Mapped[str | None] = mapped_column(String(500))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StockBalance(TimestampMixin, Base):
    """Cache of SUM(stock_movements.qty_base); rebuild with services.stock.rebuild_balances."""

    __tablename__ = "stock_balances"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, unique=True)
    qty_base: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DocSequence(TimestampMixin, Base):
    __tablename__ = "doc_sequences"
    __table_args__ = (UniqueConstraint("doc_type", "period", name="uq_doc_sequences_type_period"),)

    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # "2569-10" or "2569"
    last_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Sale(TimestampMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'voided')", name="ck_sales_status"),
        CheckConstraint("payment_type IN ('cash', 'transfer', 'credit')", name="ck_sales_payment_type"),
    )

    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    doc_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    # FK to customers is added in phase 4 together with the customers table.
    customer_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default=SALE_COMPLETED, nullable=False)

    subtotal: Mapped[int] = mapped_column(Integer, nullable=False)  # after line discounts
    discount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # bill discount
    vatable_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # tax base (ex VAT)
    exempt_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vat_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)

    payment_type: Mapped[str] = mapped_column(String(10), nullable=False)
    cash_received: Mapped[int | None] = mapped_column(Integer)
    change_amount: Mapped[int | None] = mapped_column(Integer)

    shop_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    buyer_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    vat_rate_snapshot: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # basis points

    void_reason: Mapped[str | None] = mapped_column(String(500))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    replaced_by_sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    print_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", order_by="SaleLine.id", cascade="all, delete-orphan"
    )


class SaleLine(TimestampMixin, Base):
    __tablename__ = "sale_lines"

    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_to_base: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)  # x1000, in the sale unit
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    discount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    line_total: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unit_cost_snapshot: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # satang / base unit

    sale: Mapped[Sale] = relationship(back_populates="lines")
