"""Suppliers and goods received. Money = satang, quantities = x1000."""

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

PURCHASE_PENDING_COST = "pending_cost"
PURCHASE_COMPLETED = "completed"
PURCHASE_VOIDED = "voided"

VAT_NONE = "none"  # the supplier's bill has no VAT
VAT_INCLUDED = "included"  # prices on the bill include VAT
VAT_EXCLUDED = "excluded"  # VAT is added on top of the prices


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(13))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Purchase(TimestampMixin, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("status IN ('pending_cost', 'completed', 'voided')", name="ck_purchases_status"),
        CheckConstraint("vat_type IN ('none', 'included', 'excluded')", name="ck_purchases_vat_type"),
    )

    doc_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_invoice_no: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    supplier_invoice_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), nullable=False)
    vat_type: Mapped[str] = mapped_column(String(10), default=VAT_NONE, nullable=False)
    vat_rate_bp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Totals are 0 until every line has a cost.
    subtotal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # sum of line totals as entered
    vat_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    costed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(500))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)

    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list["PurchaseLine"]] = relationship(
        back_populates="purchase", order_by="PurchaseLine.id", cascade="all, delete-orphan"
    )


class PurchaseLine(TimestampMixin, Base):
    __tablename__ = "purchase_lines"

    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_to_base: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)  # x1000, in the purchase unit
    # Cost per purchase unit as written on the supplier's bill; None = not entered yet.
    unit_cost: Mapped[int | None] = mapped_column(Integer)
    line_total: Mapped[int | None] = mapped_column(Integer)
    # Cost per BASE unit used for avg_cost (ex VAT for VAT-registered shops).
    cost_base: Mapped[int | None] = mapped_column(Integer)

    purchase: Mapped[Purchase] = relationship(back_populates="lines")
