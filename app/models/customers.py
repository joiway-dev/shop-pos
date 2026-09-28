"""Customers, quotations and receivables (SPEC phase 4). Money = satang, qty = x1000."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

QT_SENT = "sent"
QT_CONVERTED = "converted"

PAY_METHODS = {"cash": "เงินสด", "transfer": "เงินโอน", "cheque": "เช็ค"}
PAYMENT_COMPLETED = "completed"
PAYMENT_VOIDED = "voided"


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(13))
    branch: Mapped[str | None] = mapped_column(String(5))  # "00000" = head office
    credit_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # satang; 0 = no credit
    credit_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Quotation(TimestampMixin, Base):
    __tablename__ = "quotations"
    __table_args__ = (CheckConstraint("status IN ('draft', 'sent', 'converted')", name="ck_quotations_status"),)

    doc_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    # Free-text recipient when no customer record is used.
    customer_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=QT_SENT, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    bill_discount_text: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    subtotal: Mapped[int] = mapped_column(Integer, nullable=False)
    discount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vatable_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exempt_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vat_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_rate_snapshot: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shop_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    converted_sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    customer: Mapped[Customer | None] = relationship()
    lines: Mapped[list["QuotationLine"]] = relationship(
        back_populates="quotation", order_by="QuotationLine.id", cascade="all, delete-orphan"
    )

    def is_expired(self, today: date) -> bool:
        return self.status != QT_CONVERTED and self.valid_until < today


class QuotationLine(TimestampMixin, Base):
    __tablename__ = "quotation_lines"

    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"))
    sku_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_to_base: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_text: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    discount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    line_total: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    quotation: Mapped[Quotation] = relationship(back_populates="lines")


class ArPayment(TimestampMixin, Base):
    """Payment received from a credit customer (ใบรับเงิน RV)."""

    __tablename__ = "ar_payments"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'voided')", name="ck_ar_payments_status"),
        CheckConstraint("method IN ('cash', 'transfer', 'cheque')", name="ck_ar_payments_method"),
    )

    doc_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=PAYMENT_COMPLETED, nullable=False)
    shop_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    customer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    void_reason: Mapped[str | None] = mapped_column(String(500))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    print_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    customer: Mapped[Customer] = relationship()
    allocations: Mapped[list["ArAllocation"]] = relationship(
        back_populates="payment", order_by="ArAllocation.id", cascade="all, delete-orphan"
    )


class ArAllocation(TimestampMixin, Base):
    """How much of a payment settles which credit sale."""

    __tablename__ = "ar_allocations"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_ar_allocations_amount"),)

    payment_id: Mapped[int] = mapped_column(ForeignKey("ar_payments.id"), nullable=False, index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)

    payment: Mapped[ArPayment] = relationship(back_populates="allocations")
