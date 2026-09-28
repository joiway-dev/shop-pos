from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

VAT_MODE_NONE = "none"
VAT_MODE_VAT = "vat"
VAT_MODES = (VAT_MODE_NONE, VAT_MODE_VAT)

HEAD_OFFICE_BRANCH_NO = "00000"


class ShopSetting(TimestampMixin, Base):
    """Single-row table (id = 1) holding the shop's configuration."""

    __tablename__ = "shop_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_shop_settings_single_row"),
        CheckConstraint("vat_mode IN ('none', 'vat')", name="ck_shop_settings_vat_mode"),
    )

    shop_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    logo_filename: Mapped[str | None] = mapped_column(String(100))

    vat_mode: Mapped[str] = mapped_column(String(10), default=VAT_MODE_NONE, nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(13))
    # "00000" = สำนักงานใหญ่, otherwise the 5-digit branch number.
    branch_no: Mapped[str | None] = mapped_column(String(5))
    # VAT rate in basis points: 700 = 7.00%
    vat_rate_bp: Mapped[int] = mapped_column(Integer, default=700, nullable=False)
    price_includes_vat: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_abbreviated_invoice: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Product matching thresholds (SPEC 4.2)
    match_auto_accept: Mapped[int] = mapped_column(
        Integer, default=92, server_default="92", nullable=False
    )
    match_suggest_min: Mapped[int] = mapped_column(
        Integer, default=60, server_default="60", nullable=False
    )
    match_min_gap: Mapped[int] = mapped_column(Integer, default=8, server_default="8", nullable=False)

    # Document numbers restart every "monthly" (RC2569-10-0001) or "yearly" (RC2569-0001)
    doc_number_reset: Mapped[str] = mapped_column(
        String(10), default="monthly", server_default="monthly", nullable=False
    )

    # {"RC": "SB", ...}; missing types use documents.DEFAULT_PREFIXES
    doc_prefixes: Mapped[dict | None] = mapped_column(JSON)

    # Empty = <data_dir>/backups
    backup_dir: Mapped[str | None] = mapped_column(String(500))
    backup_keep_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
