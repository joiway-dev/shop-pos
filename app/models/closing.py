"""Daily cash close (ปิดยอดประจำวัน). Money = satang."""

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DailyClose(TimestampMixin, Base):
    """One row per count; a day may be closed again (the latest row counts)."""

    __tablename__ = "daily_closes"

    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    opening_cash: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cash_sales: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cash_receipts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # AR payments in cash
    expected_cash: Mapped[int] = mapped_column(Integer, nullable=False)
    counted_cash: Mapped[int] = mapped_column(Integer, nullable=False)
    difference: Mapped[int] = mapped_column(Integer, nullable=False)  # counted - expected
    transfer_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credit_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bill_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
