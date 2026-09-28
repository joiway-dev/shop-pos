"""Customer records. Staff may add/edit contact details; only the owner sets
credit limit and credit days (owner decision)."""

import re
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Customer, User
from app.services.audit import log_action
from app.services.money import AmountError, format_money, parse_money
from app.services.settings import is_valid_thai_tax_id


class CustomerError(ValueError):
    pass


@dataclass
class CustomerInput:
    name: str
    phone: str = ""
    address: str = ""
    tax_id: str = ""
    branch: str = ""  # "" / "สำนักงานใหญ่" / number
    note: str = ""
    credit_limit: str | None = None  # None = keep (non-owners cannot change)
    credit_days: str | None = None


def _clean_branch(text: str, has_tax_id: bool) -> str | None:
    text = " ".join((text or "").split())
    if text in ("", "สำนักงานใหญ่", "00000"):
        return "00000" if has_tax_id else None
    if re.fullmatch(r"\d{1,5}", text) and int(text) > 0:
        return text.zfill(5)
    raise CustomerError("สาขาต้องเป็น \"สำนักงานใหญ่\" หรือเลขสาขา")


def save_customer(db: Session, actor: User, customer: Customer | None, data: CustomerInput) -> Customer:
    name = " ".join((data.name or "").split())
    if not name:
        raise CustomerError("กรุณากรอกชื่อลูกค้า")
    if len(name) > 200:
        raise CustomerError("ชื่อลูกค้ายาวเกิน 200 ตัวอักษร")
    tax_id = re.sub(r"[\s-]", "", data.tax_id or "")
    if tax_id and not is_valid_thai_tax_id(tax_id):
        raise CustomerError("เลขประจำตัวผู้เสียภาษีไม่ถูกต้อง")
    values = {
        "name": name, "phone": (data.phone or "").strip()[:50], "address": (data.address or "").strip(),
        "tax_id": tax_id or None, "branch": _clean_branch(data.branch, bool(tax_id)),
        "note": (data.note or "").strip()[:500],
    }
    wants_credit_change = data.credit_limit is not None or data.credit_days is not None
    if wants_credit_change:
        if not actor.is_owner:
            raise CustomerError("วงเงินและเครดิตวันแก้ได้เฉพาะเจ้าของร้าน")
        try:
            values["credit_limit"] = parse_money(data.credit_limit or "0", "วงเงิน")
        except AmountError as e:
            raise CustomerError(str(e)) from None
        try:
            days = int((data.credit_days or "0").strip())
            if not 0 <= days <= 365:
                raise ValueError
        except ValueError:
            raise CustomerError("เครดิตต้องเป็นจำนวนวัน 0–365") from None
        values["credit_days"] = days

    if customer is None:
        customer = Customer(**{"credit_limit": 0, "credit_days": 0, **values}, is_active=True)
        db.add(customer)
        db.flush()
        log_action(db, actor.id, "customer_create", "customer", customer.id, {"name": name})
        return customer
    changes = {k: [getattr(customer, k), v] for k, v in values.items() if getattr(customer, k) != v}
    for k, v in values.items():
        setattr(customer, k, v)
    if changes:
        if "credit_limit" in changes:
            changes["credit_limit"] = [format_money(x) for x in changes["credit_limit"]]
        log_action(db, actor.id, "customer_update", "customer", customer.id, {"changes": changes})
    return customer


def set_customer_active(db: Session, actor: User, customer: Customer, active: bool) -> None:
    if not actor.is_owner:
        raise CustomerError("เฉพาะเจ้าของร้าน")
    if customer.is_active != active:
        customer.is_active = active
        log_action(db, actor.id, "customer_activate" if active else "customer_deactivate", "customer",
                   customer.id, {"name": customer.name})


def search_customers(db: Session, q: str = "", include_inactive: bool = False, limit: int = 50) -> list[Customer]:
    stmt = select(Customer)
    if not include_inactive:
        stmt = stmt.where(Customer.is_active.is_(True))
    q = q.strip()
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Customer.name.ilike(term), Customer.phone.ilike(term), Customer.tax_id == q))
    return list(db.scalars(stmt.order_by(Customer.name).limit(limit)))


def buyer_from_customer(c: Customer) -> dict:
    """Buyer block printed on documents."""
    return {"name": c.name, "address": c.address, "tax_id": c.tax_id, "branch_no": c.branch,
            "phone": c.phone, "customer_id": c.id}
