"""Quotations (ใบเสนอราคา QT).

A quotation is not a receipt/tax invoice, so it stays editable until it is
converted to a sale (owner decision); every save is audited. After
valid_until it shows as expired and must be renewed before converting.
Discounts given by staff need the owner PIN when the quotation is saved, so
converting it later needs no further approval.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import begin_immediate
from app.models import Customer, Quotation, QuotationLine, User
from app.models.customers import QT_CONVERTED, QT_SENT
from app.models.settings import VAT_MODE_VAT
from app.services import auth, documents
from app.services.audit import log_action
from app.services.clock import now_local
from app.services.money import format_money
from app.services.sales import CartLineInput, build_quote
from app.services.settings import get_shop_settings, shop_snapshot

DEFAULT_VALID_DAYS = 7


class QuotationError(ValueError):
    def __init__(self, message: str, pin_failed: bool = False):
        super().__init__(message)
        self.pin_failed = pin_failed


@dataclass
class QuotationInput:
    lines: list[CartLineInput]
    bill_discount: str = ""
    customer_id: int | None = None
    customer_name: str = ""
    valid_days: int = DEFAULT_VALID_DAYS
    note: str = ""
    owner_pin: str = ""


def save_quotation(db: Session, actor: User, qt: Quotation | None, data: QuotationInput) -> Quotation:
    """Create (qt=None) or replace the contents of a quotation. Caller commits;
    on pin_failed the caller should commit to keep the failed-PIN counter."""
    if qt is not None and qt.status == QT_CONVERTED:
        raise QuotationError("ใบเสนอราคานี้แปลงเป็นบิลขายแล้ว แก้ไขไม่ได้")
    if not data.lines:
        raise QuotationError("ยังไม่มีสินค้าในใบเสนอราคา")
    if not 1 <= int(data.valid_days) <= 365:
        raise QuotationError("อายุใบเสนอราคาต้องเป็น 1–365 วัน")
    if qt is None:
        begin_immediate(db)  # a new QT number is issued below
    settings = get_shop_settings(db)
    q = build_quote(db, data.lines, data.bill_discount, settings)
    if not q.ok:
        problems = q.errors + [f"{l.name or 'รายการ'}: {l.error}" for l in q.lines if l.error]
        raise QuotationError(problems[0])
    customer = None
    if data.customer_id:
        customer = db.get(Customer, data.customer_id)
        if customer is None or not customer.is_active:
            raise QuotationError("ไม่พบลูกค้า")
    approver = None
    if q.has_discount and not actor.is_owner:
        approver = auth.verify_owner_pin(db, data.owner_pin or "")
        if approver is None:
            raise QuotationError("การให้ส่วนลดต้องใช้ PIN เจ้าของร้าน (PIN ไม่ถูกต้อง)", pin_failed=True)

    totals = q.totals
    today = now_local().date()
    values = dict(
        customer_id=customer.id if customer else None,
        customer_name=customer.name if customer else " ".join((data.customer_name or "").split())[:200],
        valid_until=today + timedelta(days=int(data.valid_days)),
        bill_discount_text=(data.bill_discount or "").strip()[:20],
        subtotal=totals.subtotal, discount=totals.discount, vatable_amount=totals.vatable_amount,
        exempt_amount=totals.exempt_amount, vat_amount=totals.vat_amount, total=totals.total,
        vat_rate_snapshot=settings.vat_rate_bp if settings.vat_mode == VAT_MODE_VAT else 0,
        shop_snapshot=shop_snapshot(settings), note=(data.note or "").strip()[:500],
    )
    new_lines = [
        QuotationLine(
            product_id=l.product_id, unit_id=l.unit_id, sku_snapshot=l.sku, product_name_snapshot=l.name,
            unit_name=l.unit_name, factor_to_base=l.factor_to_base, qty=l.qty, unit_price=l.unit_price,
            discount_text=(src.discount or "").strip()[:20], discount=l.discount, line_total=l.line_total,
            vat_exempt=l.vat_exempt,
        )
        for l, src in zip(q.lines, data.lines)
    ]
    if qt is None:
        doc_no = documents.next_doc_no(db, documents.DOC_QT, now_local(), settings.doc_number_reset,
                                       documents.prefix_for(settings, documents.DOC_QT))
        qt = Quotation(doc_no=doc_no, status=QT_SENT, user_id=actor.id, lines=new_lines, **values)
        db.add(qt)
        db.flush()
        action = "quotation_create"
    else:
        for k, v in values.items():
            setattr(qt, k, v)
        qt.lines = new_lines
        db.flush()
        action = "quotation_update"
    log_action(db, actor.id, action, "quotation", qt.id, {
        "doc_no": qt.doc_no, "total": format_money(qt.total), "customer": qt.customer_name,
        "discount_approved_by": approver.id if approver else None,
    })
    return qt


def renew(db: Session, actor: User, qt: Quotation, days: int = DEFAULT_VALID_DAYS) -> None:
    if qt.status == QT_CONVERTED:
        raise QuotationError("ใบเสนอราคานี้แปลงเป็นบิลขายแล้ว")
    if not 1 <= days <= 365:
        raise QuotationError("อายุใบเสนอราคาต้องเป็น 1–365 วัน")
    old = qt.valid_until
    qt.valid_until = now_local().date() + timedelta(days=days)
    log_action(db, actor.id, "quotation_renew", "quotation", qt.id,
               {"doc_no": qt.doc_no, "valid_until": [old.isoformat(), qt.valid_until.isoformat()]})


def get_quotation(db: Session, quotation_id: int) -> Quotation | None:
    return db.scalar(
        select(Quotation).where(Quotation.id == quotation_id)
        .options(selectinload(Quotation.lines), selectinload(Quotation.customer))
    )


def status_of(qt: Quotation, today: date | None = None) -> str:
    """'converted' | 'expired' | 'open'"""
    if qt.status == QT_CONVERTED:
        return "converted"
    return "expired" if qt.is_expired(today or now_local().date()) else "open"


def list_quotations(db: Session, q: str = "", status: str = "", limit: int = 50, offset: int = 0):
    today = now_local().date()
    stmt = select(Quotation)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Quotation.doc_no.ilike(term), Quotation.customer_name.ilike(term)))
    if status == "converted":
        stmt = stmt.where(Quotation.status == QT_CONVERTED)
    elif status == "open":
        stmt = stmt.where(Quotation.status != QT_CONVERTED, Quotation.valid_until >= today)
    elif status == "expired":
        stmt = stmt.where(Quotation.status != QT_CONVERTED, Quotation.valid_until < today)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Quotation.id.desc()).limit(limit).offset(offset))
    return list(rows), total
