"""Accounts receivable: open credit bills, payments (RV) and aging (SPEC phase 4).

A credit sale's outstanding amount = total - allocations of non-voided payments.
Nothing is cached, so voiding a payment immediately re-opens the bills it paid.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import begin_immediate
from app.models import ArAllocation, ArPayment, Customer, Sale, User
from app.models.base import utcnow
from app.models.customers import PAY_METHODS, PAYMENT_COMPLETED, PAYMENT_VOIDED
from app.models.sales import PAY_CREDIT, SALE_COMPLETED
from app.services import auth, documents
from app.services.audit import log_action
from app.services.clock import now_local, to_local
from app.services.customers import buyer_from_customer
from app.services.money import AmountError, format_money, parse_money
from app.services.settings import get_shop_settings, shop_snapshot

AGING_BUCKETS = (("0–30 วัน", 0, 30), ("31–60 วัน", 31, 60), ("61–90 วัน", 61, 90), ("เกิน 90 วัน", 91, None))


class ReceivableError(ValueError):
    def __init__(self, message: str, pin_failed: bool = False):
        super().__init__(message)
        self.pin_failed = pin_failed


@dataclass
class OpenBill:
    sale: Sale
    paid: int
    outstanding: int
    age_days: int
    overdue: bool


# --- balances ------------------------------------------------------------------


def _paid_by_sale(db: Session, sale_ids: list[int] | None = None) -> dict[int, int]:
    stmt = (
        select(ArAllocation.sale_id, func.sum(ArAllocation.amount))
        .join(ArPayment, ArPayment.id == ArAllocation.payment_id)
        .where(ArPayment.status == PAYMENT_COMPLETED)
        .group_by(ArAllocation.sale_id)
    )
    if sale_ids is not None:
        stmt = stmt.where(ArAllocation.sale_id.in_(sale_ids))
    return {sid: int(total) for sid, total in db.execute(stmt)}


def sale_paid(db: Session, sale_id: int) -> int:
    return _paid_by_sale(db, [sale_id]).get(sale_id, 0)


def open_bills(db: Session, customer_id: int | None = None, today: date | None = None) -> list[OpenBill]:
    """Unpaid (or partly paid) credit sales, oldest first."""
    today = today or now_local().date()
    stmt = select(Sale).where(Sale.payment_type == PAY_CREDIT, Sale.status == SALE_COMPLETED)
    if customer_id is not None:
        stmt = stmt.where(Sale.customer_id == customer_id)
    sales = list(db.scalars(stmt.order_by(Sale.created_at, Sale.id)))
    paid = _paid_by_sale(db, [s.id for s in sales])
    bills = []
    for s in sales:
        outstanding = s.total - paid.get(s.id, 0)
        if outstanding > 0:
            issued = to_local(s.created_at).date()
            bills.append(OpenBill(s, paid.get(s.id, 0), outstanding, (today - issued).days,
                                  bool(s.due_date and s.due_date < today)))
    return bills


def customer_balance(db: Session, customer_id: int) -> int:
    return sum(b.outstanding for b in open_bills(db, customer_id))


@dataclass
class AgingRow:
    customer: Customer
    buckets: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    overdue: int = 0

    @property
    def total(self) -> int:
        return sum(self.buckets)


def aging(db: Session, today: date | None = None) -> list[AgingRow]:
    """Outstanding per customer split by invoice age (owner decision: from the bill date)."""
    rows: dict[int, AgingRow] = {}
    for bill in open_bills(db, today=today):
        cid = bill.sale.customer_id
        if cid not in rows:
            rows[cid] = AgingRow(db.get(Customer, cid))
        row = rows[cid]
        for i, (_, low, high) in enumerate(AGING_BUCKETS):
            if bill.age_days >= low and (high is None or bill.age_days <= high):
                row.buckets[i] += bill.outstanding
                break
        if bill.overdue:
            row.overdue += bill.outstanding
    return sorted(rows.values(), key=lambda r: (-r.total, r.customer.name))


# --- payments ------------------------------------------------------------------


def receive_payment(
    db: Session, actor: User, customer: Customer, amount_text: str, method: str, note: str = "",
    allocations: dict[int, str] | None = None,
) -> ArPayment:
    """Record a payment and settle bills: the given allocations, or oldest first.
    Overpayment is refused (the shop keeps no customer credit balance)."""
    if method not in PAY_METHODS:
        raise ReceivableError("วิธีรับชำระไม่ถูกต้อง")
    try:
        amount = parse_money(amount_text, "ยอดรับชำระ")
    except AmountError as e:
        raise ReceivableError(str(e)) from None
    if amount <= 0:
        raise ReceivableError("ยอดรับชำระต้องมากกว่า 0")

    begin_immediate(db)
    bills = {b.sale.id: b for b in open_bills(db, customer.id)}
    total_open = sum(b.outstanding for b in bills.values())
    if not bills:
        raise ReceivableError("ลูกค้ารายนี้ไม่มียอดค้างชำระ")
    if amount > total_open:
        raise ReceivableError(f"รับเกินยอดค้าง (ค้างทั้งหมด {format_money(total_open)} บาท)")

    plan: list[tuple[int, int]] = []
    if allocations:
        for sale_id, text in allocations.items():
            if not (text or "").strip():
                continue
            bill = bills.get(sale_id)
            if bill is None:
                raise ReceivableError("บิลที่เลือกไม่ได้ค้างชำระ")
            try:
                part = parse_money(text, "ยอดตัดบิล")
            except AmountError as e:
                raise ReceivableError(str(e)) from None
            if part > bill.outstanding:
                raise ReceivableError(f"ตัดบิล {bill.sale.doc_no} เกินยอดค้าง ({format_money(bill.outstanding)})")
            if part:
                plan.append((sale_id, part))
        if sum(p for _, p in plan) != amount:
            raise ReceivableError("ยอดที่ตัดแต่ละบิลรวมกันต้องเท่ากับยอดรับชำระ")
    else:
        left = amount
        for bill in bills.values():  # oldest first
            part = min(left, bill.outstanding)
            plan.append((bill.sale.id, part))
            left -= part
            if not left:
                break

    settings = get_shop_settings(db)
    doc_no = documents.next_doc_no(db, documents.DOC_RV, now_local(), settings.doc_number_reset,
                                   documents.prefix_for(settings, documents.DOC_RV))
    payment = ArPayment(
        doc_no=doc_no, customer_id=customer.id, amount=amount, method=method, note=(note or "").strip()[:500],
        status=PAYMENT_COMPLETED, shop_snapshot=shop_snapshot(settings),
        customer_snapshot=buyer_from_customer(customer), print_count=0, user_id=actor.id,
    )
    for sale_id, part in plan:
        payment.allocations.append(ArAllocation(sale_id=sale_id, amount=part))
    db.add(payment)
    db.flush()
    log_action(db, actor.id, "ar_payment", "ar_payment", payment.id, {
        "doc_no": doc_no, "customer": customer.name, "amount": format_money(amount), "method": method,
        "bills": [{"sale_id": s, "amount": format_money(a)} for s, a in plan],
    })
    return payment


def void_payment(db: Session, actor: User, payment: ArPayment, reason: str, owner_pin: str) -> ArPayment:
    reason = " ".join((reason or "").split())
    if payment.status == PAYMENT_VOIDED:
        raise ReceivableError("ใบรับเงินนี้ถูกยกเลิกไปแล้ว")
    if len(reason) < 3:
        raise ReceivableError("กรุณาระบุเหตุผลที่ยกเลิก")
    approver = auth.verify_owner_pin(db, owner_pin or "")
    if approver is None:
        raise ReceivableError("PIN เจ้าของร้านไม่ถูกต้อง", pin_failed=True)
    payment.status = PAYMENT_VOIDED
    payment.void_reason = reason[:500]
    payment.voided_by = approver.id
    payment.voided_at = utcnow()
    log_action(db, actor.id, "ar_payment_void", "ar_payment", payment.id,
               {"doc_no": payment.doc_no, "reason": payment.void_reason, "approved_by": approver.id})
    return payment


def get_payment(db: Session, payment_id: int) -> ArPayment | None:
    return db.scalar(
        select(ArPayment).where(ArPayment.id == payment_id)
        .options(selectinload(ArPayment.allocations), selectinload(ArPayment.customer))
    )


def payments_of(db: Session, customer_id: int) -> list[ArPayment]:
    return list(db.scalars(
        select(ArPayment).where(ArPayment.customer_id == customer_id).order_by(ArPayment.id.desc())
    ))


def record_print(db: Session, actor: User, payment: ArPayment) -> bool:
    is_copy = payment.print_count > 0
    payment.print_count += 1
    if is_copy:
        log_action(db, actor.id, "ar_payment_reprint", "ar_payment", payment.id, {"doc_no": payment.doc_no})
    return is_copy
