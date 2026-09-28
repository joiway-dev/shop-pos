import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Supplier
from app.services.audit import log_action
from app.services.settings import is_valid_thai_tax_id


class SupplierError(ValueError):
    pass


def list_suppliers(db: Session, include_inactive: bool = True) -> list[Supplier]:
    stmt = select(Supplier).order_by(Supplier.is_active.desc(), Supplier.name)
    if not include_inactive:
        stmt = stmt.where(Supplier.is_active.is_(True))
    return list(db.scalars(stmt))


def _validate(db: Session, name: str, tax_id: str, exclude_id: int | None) -> tuple[str, str | None]:
    name = " ".join((name or "").split())
    if not name:
        raise SupplierError("กรุณากรอกชื่อผู้ขาย")
    if len(name) > 200:
        raise SupplierError("ชื่อผู้ขายยาวเกิน 200 ตัวอักษร")
    stmt = select(Supplier.id).where(func.lower(Supplier.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Supplier.id != exclude_id)
    if db.scalar(stmt):
        raise SupplierError("มีผู้ขายชื่อนี้แล้ว")
    tax_id = re.sub(r"[\s-]", "", tax_id or "")
    if tax_id and not is_valid_thai_tax_id(tax_id):
        raise SupplierError("เลขประจำตัวผู้เสียภาษีไม่ถูกต้อง")
    return name, tax_id or None


def save_supplier(
    db: Session, actor_id: int, supplier: Supplier | None, name: str, phone: str = "", address: str = "",
    tax_id: str = "",
) -> Supplier:
    name, tax_id = _validate(db, name, tax_id, supplier.id if supplier else None)
    values = {"name": name, "phone": (phone or "").strip()[:50], "address": (address or "").strip(), "tax_id": tax_id}
    if supplier is None:
        supplier = Supplier(**values, is_active=True)
        db.add(supplier)
        db.flush()
        log_action(db, actor_id, "supplier_create", "supplier", supplier.id, {"name": name})
        return supplier
    changes = {k: [getattr(supplier, k), v] for k, v in values.items() if getattr(supplier, k) != v}
    for k, v in values.items():
        setattr(supplier, k, v)
    if changes:
        log_action(db, actor_id, "supplier_update", "supplier", supplier.id, {"changes": changes})
    return supplier


def set_supplier_active(db: Session, actor_id: int, supplier: Supplier, active: bool) -> None:
    if supplier.is_active != active:
        supplier.is_active = active
        log_action(db, actor_id, "supplier_activate" if active else "supplier_deactivate", "supplier",
                   supplier.id, {"name": supplier.name})
