"""Goods received (ใบรับสินค้า).

Two ways of working (owner decision):
- A (default): staff with "can receive stock" record quantities only; the
  purchase waits in status "pending_cost" until someone allowed to see costs
  enters them.
- B: a user with "can see cost" (and the owner) can enter costs straight away.

Stock goes up as soon as the goods are received; avg_cost is replayed from the
movement history whenever costs are entered or a purchase is voided.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import begin_immediate
from app.models import Product, Purchase, PurchaseLine, StockMovement, Supplier, User
from app.models.base import utcnow
from app.models.purchasing import (
    PURCHASE_COMPLETED, PURCHASE_PENDING_COST, PURCHASE_VOIDED, VAT_EXCLUDED, VAT_INCLUDED, VAT_NONE,
)
from app.models.sales import MOVE_PURCHASE
from app.models.settings import VAT_MODE_VAT
from app.services import auth, costing, documents, stock
from app.services.audit import log_action
from app.services.clock import now_local
from app.services.money import AmountError, format_money, parse_money, parse_qty
from app.services.pricing import base_qty, line_gross, round_half_up
from app.services.settings import get_shop_settings

VAT_TYPES = (VAT_NONE, VAT_INCLUDED, VAT_EXCLUDED)
MAX_LINES = 200


class PurchaseError(ValueError):
    def __init__(self, message: str, pin_failed: bool = False):
        super().__init__(message)
        self.pin_failed = pin_failed


@dataclass
class PurchaseLineInput:
    product_id: int
    unit_id: int
    qty: str
    unit_cost: str = ""  # blank = not entered (status "รอใส่ต้นทุน")


@dataclass
class PurchaseInput:
    lines: list[PurchaseLineInput]
    supplier_id: int | None = None
    supplier_invoice_no: str = ""
    supplier_invoice_date: str = ""  # YYYY-MM-DD
    vat_type: str = VAT_NONE
    note: str = ""


# --- cost arithmetic -------------------------------------------------------------


def cost_per_base(unit_cost: int, factor_to_base: int, vat_type: str, vat_rate_bp: int, shop_vat: bool) -> int:
    """Cost of one base unit for avg_cost.

    VAT-registered shops reclaim input VAT, so their cost excludes VAT; other
    shops carry the VAT they paid as part of the cost.
    """
    price = Decimal(unit_cost)
    rate = Decimal(vat_rate_bp)
    if vat_type == VAT_INCLUDED and shop_vat:
        price = price * 10000 / (10000 + rate)
    elif vat_type == VAT_EXCLUDED and not shop_vat:
        price = price * (10000 + rate) / 10000
    return round_half_up(price * 1000 / factor_to_base)


def bill_totals(line_totals: list[int], vat_type: str, vat_rate_bp: int) -> tuple[int, int, int]:
    """(subtotal, vat, total) of the supplier's bill; VAT rounded once per bill."""
    subtotal = sum(line_totals)
    rate = Decimal(vat_rate_bp)
    if vat_type == VAT_INCLUDED:
        vat = round_half_up(Decimal(subtotal) * rate / (10000 + rate))
        return subtotal, vat, subtotal
    if vat_type == VAT_EXCLUDED:
        vat = round_half_up(Decimal(subtotal) * rate / 10000)
        return subtotal, vat, subtotal + vat
    return subtotal, 0, subtotal


def _apply_costs(purchase: Purchase, costs: list[int], shop_vat: bool) -> None:
    for line, unit_cost in zip(purchase.lines, costs):
        line.unit_cost = unit_cost
        line.line_total = line_gross(line.qty, unit_cost)
        line.cost_base = cost_per_base(unit_cost, line.factor_to_base, purchase.vat_type, purchase.vat_rate_bp,
                                       shop_vat)
    purchase.subtotal, purchase.vat_amount, purchase.total = bill_totals(
        [l.line_total for l in purchase.lines], purchase.vat_type, purchase.vat_rate_bp
    )
    purchase.status = PURCHASE_COMPLETED


# --- create ----------------------------------------------------------------------


def _parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PurchaseError("วันที่ใบแจ้งหนี้ไม่ถูกต้อง") from None


def create_purchase(db: Session, actor: User, data: PurchaseInput) -> Purchase:
    """Receive goods (caller commits)."""
    if not actor.may_receive_stock:
        raise PurchaseError("ไม่มีสิทธิ์รับสินค้าเข้า")
    lines = [l for l in data.lines if l.product_id]
    if not lines:
        raise PurchaseError("ยังไม่มีสินค้าในใบรับ")
    if len(lines) > MAX_LINES:
        raise PurchaseError(f"ใบรับหนึ่งมีได้ไม่เกิน {MAX_LINES} รายการ")
    if data.vat_type not in VAT_TYPES:
        raise PurchaseError("รูปแบบ VAT ไม่ถูกต้อง")
    if any(l.unit_cost.strip() for l in lines) and not actor.may_see_cost:
        raise PurchaseError("ไม่มีสิทธิ์ใส่ต้นทุน")

    begin_immediate(db)
    settings = get_shop_settings(db)
    shop_vat = settings.vat_mode == VAT_MODE_VAT
    supplier = None
    if data.supplier_id:
        supplier = db.get(Supplier, data.supplier_id)
        if supplier is None or not supplier.is_active:
            raise PurchaseError("ไม่พบผู้ขาย")
    invoice_date = _parse_date(data.supplier_invoice_date)

    products = {
        p.id: p for p in db.scalars(
            select(Product).where(Product.id.in_({l.product_id for l in lines})).options(selectinload(Product.units))
        )
    }
    purchase = Purchase(
        doc_no="", supplier_id=supplier.id if supplier else None,
        supplier_invoice_no=(data.supplier_invoice_no or "").strip()[:50], supplier_invoice_date=invoice_date,
        status=PURCHASE_PENDING_COST, vat_type=data.vat_type,
        vat_rate_bp=settings.vat_rate_bp if data.vat_type != VAT_NONE else 0,
        note=(data.note or "").strip()[:500], user_id=actor.id,
    )
    costs: list[int | None] = []
    for n, l in enumerate(lines, start=1):
        product = products.get(l.product_id)
        if product is None or not product.is_active:
            raise PurchaseError(f"รายการที่ {n}: ไม่พบสินค้า")
        unit = next((u for u in product.units if u.id == l.unit_id), None)
        if unit is None:
            raise PurchaseError(f"รายการที่ {n}: ไม่พบหน่วยนี้ในสินค้า")
        try:
            qty = parse_qty(l.qty)
            costs.append(parse_money(l.unit_cost, "ราคาทุน") if l.unit_cost.strip() else None)
        except AmountError as e:
            raise PurchaseError(f"รายการที่ {n} ({product.name}): {e}") from None
        purchase.lines.append(PurchaseLine(
            product_id=product.id, product_name_snapshot=product.name, unit_name=unit.unit_name,
            factor_to_base=unit.factor_to_base, qty=qty,
        ))

    if all(c is not None for c in costs):
        _apply_costs(purchase, costs, shop_vat)
        purchase.costed_by = actor.id
    elif any(c is not None for c in costs):
        raise PurchaseError("ใส่ต้นทุนให้ครบทุกรายการ หรือเว้นว่างทั้งหมดเพื่อให้เจ้าของร้านใส่ภายหลัง")

    purchase.doc_no = documents.next_doc_no(
        db, documents.DOC_GR, now_local(), settings.doc_number_reset, documents.prefix_for(settings, documents.DOC_GR)
    )
    db.add(purchase)
    db.flush()
    for line in purchase.lines:
        stock.record_movement(db, line.product_id, base_qty(line.qty, line.factor_to_base), MOVE_PURCHASE,
                              actor.id, ref_type=costing.REF_PURCHASE_LINE, ref_id=line.id,
                              unit_cost=line.cost_base or 0)
    for product_id in {l.product_id for l in purchase.lines}:
        costing.recompute_avg_cost(db, product_id)
    log_action(db, actor.id, "purchase_create", "purchase", purchase.id, {
        "doc_no": purchase.doc_no, "status": purchase.status, "lines": len(purchase.lines),
        "total": format_money(purchase.total) if purchase.status == PURCHASE_COMPLETED else None,
    })
    return purchase


# --- enter costs later / void ------------------------------------------------------


def enter_costs(db: Session, actor: User, purchase: Purchase, unit_costs: dict[int, str], vat_type: str) -> Purchase:
    """Fill in costs of a "pending_cost" purchase (caller commits)."""
    if not actor.may_see_cost:
        raise PurchaseError("ไม่มีสิทธิ์ใส่ต้นทุน")
    if purchase.status != PURCHASE_PENDING_COST:
        raise PurchaseError("ใบรับนี้ไม่ได้รอใส่ต้นทุน")
    if vat_type not in VAT_TYPES:
        raise PurchaseError("รูปแบบ VAT ไม่ถูกต้อง")
    costs = []
    for n, line in enumerate(purchase.lines, start=1):
        text = (unit_costs.get(line.id) or "").strip()
        if not text:
            raise PurchaseError(f"รายการที่ {n} ({line.product_name_snapshot}): กรุณาใส่ราคาทุน")
        try:
            costs.append(parse_money(text, "ราคาทุน"))
        except AmountError as e:
            raise PurchaseError(f"รายการที่ {n} ({line.product_name_snapshot}): {e}") from None
    settings = get_shop_settings(db)
    purchase.vat_type = vat_type
    purchase.vat_rate_bp = settings.vat_rate_bp if vat_type != VAT_NONE else 0
    _apply_costs(purchase, costs, settings.vat_mode == VAT_MODE_VAT)
    purchase.costed_by = actor.id
    db.flush()
    for line in purchase.lines:
        for m in _line_movements(db, line.id):
            m.unit_cost = line.cost_base
    for product_id in {l.product_id for l in purchase.lines}:
        costing.recompute_avg_cost(db, product_id)
    log_action(db, actor.id, "purchase_cost", "purchase", purchase.id, {
        "doc_no": purchase.doc_no, "total": format_money(purchase.total),
        "costs": [{"product": l.product_name_snapshot, "unit_cost": format_money(l.unit_cost)} for l in purchase.lines],
    })
    return purchase


def _line_movements(db: Session, line_id: int):
    return db.scalars(select(StockMovement).where(
        StockMovement.ref_type == costing.REF_PURCHASE_LINE, StockMovement.ref_id == line_id
    ))


def void_purchase(db: Session, actor: User, purchase: Purchase, reason: str, owner_pin: str) -> Purchase:
    """Void with owner PIN + reason: stock is taken back out and avg_cost is replayed.
    On PurchaseError(pin_failed=True) the caller should commit (failed-PIN counter)."""
    reason = " ".join((reason or "").split())
    if purchase.status == PURCHASE_VOIDED:
        raise PurchaseError("ใบรับนี้ถูกยกเลิกไปแล้ว")
    if len(reason) < 3:
        raise PurchaseError("กรุณาระบุเหตุผลที่ยกเลิก")
    approver = auth.verify_owner_pin(db, owner_pin or "")
    if approver is None:
        raise PurchaseError("PIN เจ้าของร้านไม่ถูกต้อง", pin_failed=True)
    purchase.status = PURCHASE_VOIDED
    purchase.void_reason = reason[:500]
    purchase.voided_by = approver.id
    purchase.voided_at = utcnow()
    for line in purchase.lines:
        stock.record_movement(db, line.product_id, -base_qty(line.qty, line.factor_to_base), MOVE_PURCHASE,
                              actor.id, ref_type=costing.REF_PURCHASE_VOID, ref_id=line.id,
                              unit_cost=line.cost_base or 0, note=f"ยกเลิก {purchase.doc_no}")
    for product_id in {l.product_id for l in purchase.lines}:
        costing.recompute_avg_cost(db, product_id)
    log_action(db, actor.id, "purchase_void", "purchase", purchase.id,
               {"doc_no": purchase.doc_no, "reason": purchase.void_reason, "approved_by": approver.id})
    return purchase


# --- queries -----------------------------------------------------------------------


def get_purchase(db: Session, purchase_id: int) -> Purchase | None:
    return db.scalar(
        select(Purchase).where(Purchase.id == purchase_id)
        .options(selectinload(Purchase.lines), selectinload(Purchase.supplier))
    )


def list_purchases(db: Session, q: str = "", status: str = "", limit: int = 50, offset: int = 0):
    stmt = select(Purchase).options(selectinload(Purchase.supplier))
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.outerjoin(Supplier).where(or_(
            Purchase.doc_no.ilike(term), Purchase.supplier_invoice_no.ilike(term), Supplier.name.ilike(term),
            Purchase.id.in_(select(PurchaseLine.purchase_id).where(PurchaseLine.product_name_snapshot.ilike(term))),
        ))
    if status in (PURCHASE_PENDING_COST, PURCHASE_COMPLETED, PURCHASE_VOIDED):
        stmt = stmt.where(Purchase.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Purchase.id.desc()).limit(limit).offset(offset))
    return list(rows), total


def pending_cost_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Purchase).where(Purchase.status == PURCHASE_PENDING_COST))
