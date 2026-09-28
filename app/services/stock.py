"""Stock: every change is a row in stock_movements (CLAUDE.md rule 5).

stock_balances is only a cache of SUM(qty_base) per product and can be
rebuilt at any time with `rebuild_balances`.
"""

from decimal import Decimal

from sqlalchemy import delete, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import Product, StockBalance, StockMovement, User
from app.models.sales import MOVE_ADJUST
from app.services.audit import log_action
from app.services.matching.normalize import normalize
from app.services.money import AmountError, format_qty, parse_qty
from app.services.pricing import round_half_up


def record_movement(
    db: Session,
    product_id: int,
    qty_base: int,
    type: str,
    user_id: int | None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    unit_cost: int = 0,
    note: str | None = None,
) -> StockMovement:
    """Add a movement and update the cached balance (caller commits).
    Negative balances are allowed; callers warn the user."""
    movement = StockMovement(
        product_id=product_id, qty_base=qty_base, type=type, ref_type=ref_type, ref_id=ref_id,
        unit_cost=unit_cost, note=note, user_id=user_id,
    )
    db.add(movement)
    balance = db.scalar(select(StockBalance).where(StockBalance.product_id == product_id))
    if balance is None:
        balance = StockBalance(product_id=product_id, qty_base=0)
        db.add(balance)
    balance.qty_base += qty_base
    db.flush()
    return movement


def balances(db: Session, product_ids: list[int] | None = None) -> dict[int, int]:
    stmt = select(StockBalance.product_id, StockBalance.qty_base)
    if product_ids is not None:
        stmt = stmt.where(StockBalance.product_id.in_(product_ids))
    return {pid: qty for pid, qty in db.execute(stmt)}


def balance_of(db: Session, product_id: int) -> int:
    return balances(db, [product_id]).get(product_id, 0)


class StockError(ValueError):
    pass


REF_COUNT = "count"
REF_WRITEOFF = "writeoff"
WRITEOFF_KINDS = {"damaged": "ชำรุด", "lost": "สูญหาย", "used": "ใช้ในร้าน", "other": "อื่นๆ"}


def _check_owner(actor: User) -> None:
    if not actor.is_owner:
        raise StockError("ปรับสต็อกได้เฉพาะเจ้าของร้าน")


def _reason(reason: str) -> str:
    reason = " ".join((reason or "").split())
    if len(reason) < 3:
        raise StockError("กรุณาระบุเหตุผล")
    return reason[:500]


def adjust_to_count(db: Session, actor: User, product: Product, counted: str, reason: str) -> StockMovement:
    """Stock count: set the balance to what was physically counted (base units)."""
    _check_owner(actor)
    reason = _reason(reason)
    try:
        target = parse_qty(counted, "จำนวนที่นับได้", allow_zero=True)
    except AmountError as e:
        raise StockError(str(e)) from None
    diff = target - balance_of(db, product.id)
    if diff == 0:
        raise StockError("ยอดที่นับได้ตรงกับในระบบแล้ว ไม่ต้องปรับ")
    movement = record_movement(db, product.id, diff, MOVE_ADJUST, actor.id, ref_type=REF_COUNT,
                               unit_cost=product.avg_cost, note=reason)
    log_action(db, actor.id, "stock_adjust", "product", product.id,
               {"mode": "count", "counted": format_qty(target), "diff": format_qty(diff), "reason": reason})
    return movement


def write_off(db: Session, actor: User, product: Product, qty: str, kind: str, reason: str) -> StockMovement:
    """Take damaged / lost stock out (base units)."""
    _check_owner(actor)
    if kind not in WRITEOFF_KINDS:
        raise StockError("ประเภทไม่ถูกต้อง")
    reason = _reason(reason)
    try:
        amount = parse_qty(qty, "จำนวน")
    except AmountError as e:
        raise StockError(str(e)) from None
    note = f"{WRITEOFF_KINDS[kind]}: {reason}"
    movement = record_movement(db, product.id, -amount, MOVE_ADJUST, actor.id, ref_type=REF_WRITEOFF,
                               unit_cost=product.avg_cost, note=note)
    log_action(db, actor.id, "stock_adjust", "product", product.id,
               {"mode": "writeoff", "qty": format_qty(amount), "kind": kind, "reason": reason})
    return movement


# --- stock page / stock card ------------------------------------------------------


def stock_rows(
    db: Session, q: str = "", category_id: int | None = None, low_only: bool = False,
    limit: int = 100, offset: int = 0,
) -> tuple[list[tuple[Product, int]], int]:
    """(product, balance) for the stock page; low_only = at or below min stock."""
    bal = func.coalesce(StockBalance.qty_base, 0)
    stmt = (
        select(Product, bal.label("balance"))
        .outerjoin(StockBalance, StockBalance.product_id == Product.id)
        .where(Product.is_active.is_(True))
    )
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if q.strip():
        nq = normalize(q)
        stmt = stmt.where(or_(Product.name_normalized.contains(nq) if nq else false(), Product.sku == q.strip()))
    if low_only:
        stmt = stmt.where(Product.min_stock_qty > 0, bal <= Product.min_stock_qty)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Product.name).limit(limit).offset(offset)).all()
    return [(p, int(b)) for p, b in rows], total


def low_stock_count(db: Session) -> int:
    return stock_rows(db, low_only=True, limit=1)[1]


def stock_value(db: Session) -> int:
    """Sum of positive balances x avg cost (satang)."""
    rows = db.execute(
        select(StockBalance.qty_base, Product.avg_cost)
        .join(Product, Product.id == StockBalance.product_id)
        .where(Product.is_active.is_(True), StockBalance.qty_base > 0)
    ).all()
    return sum(round_half_up(Decimal(q) * c / 1000) for q, c in rows)


def movements_of(db: Session, product_id: int, limit: int = 300) -> list[tuple[StockMovement, int]]:
    """Newest first, each with the running balance after that movement."""
    moves = list(db.scalars(
        select(StockMovement).where(StockMovement.product_id == product_id).order_by(StockMovement.id)
    ))
    running, out = 0, []
    for m in moves:
        running += m.qty_base
        out.append((m, running))
    return list(reversed(out))[:limit]


def rebuild_balances(db: Session) -> int:
    """Recompute the cache from movements. Returns the number of products."""
    totals = db.execute(
        select(StockMovement.product_id, func.sum(StockMovement.qty_base)).group_by(StockMovement.product_id)
    ).all()
    db.execute(delete(StockBalance))
    for product_id, qty in totals:
        db.add(StockBalance(product_id=product_id, qty_base=int(qty)))
    db.flush()
    return len(totals)
