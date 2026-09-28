from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ROLE_OWNER = "owner"
ROLE_CASHIER = "cashier"
ROLES = (ROLE_OWNER, ROLE_CASHIER)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'cashier')", name="ck_users_role"),)

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Consecutive wrong PINs; reset on success. See services.auth lockout policy.
    failed_pin_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # Per-staff permissions granted by the owner (owners always have both).
    # can_receive_stock: record goods received (quantities only).
    # can_see_cost: see/enter costs -- an owner-approved exception to rule 7.
    can_receive_stock: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    can_see_cost: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def may_receive_stock(self) -> bool:
        return self.is_owner or self.can_receive_stock

    @property
    def may_see_cost(self) -> bool:
        return self.is_owner or self.can_see_cost
