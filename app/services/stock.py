"""Stock: every change is a row in stock_movements (CLAUDE.md rule 5).

stock_balances is only a cache of SUM(qty_base) per product and can be
rebuilt at any time with `rebuild_balances`.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import StockBalance, StockMovement


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
