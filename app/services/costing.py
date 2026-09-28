"""Moving-average cost (SPEC phase 3).

    new_avg = (qty_before x avg_before + qty_in x cost_in) / (qty_before + qty_in)
    if qty_before <= 0 the new cost is used as is

`products.avg_cost` is always the result of replaying the product's stock
movements in order (`recompute_avg_cost`). Replaying (instead of updating
incrementally) keeps the number right when a cost is entered later
("รอใส่ต้นทุน") or a purchase is voided.

Replay rules per movement:
- purchase with a known cost, purchase not voided -> moving average
- purchase without a cost yet, or voided purchase  -> quantity only
- opening balance (adjust, ref_type "opening")      -> cost set to the given value
- everything else (sales, voids, counts, write-offs) -> quantity only
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Purchase, PurchaseLine, StockMovement
from app.models.purchasing import PURCHASE_VOIDED
from app.services.pricing import round_half_up

REF_PURCHASE_LINE = "purchase_line"
REF_PURCHASE_VOID = "purchase_void"
REF_OPENING = "opening"


def moving_average(qty_before: int, avg_before: int, qty_in: int, cost_in: int) -> int:
    """Quantities x1000, costs satang per base unit."""
    if qty_before <= 0:
        return cost_in
    total_qty = qty_before + qty_in
    if total_qty <= 0:
        return avg_before
    value = Decimal(qty_before) * avg_before + Decimal(qty_in) * cost_in
    return round_half_up(value / total_qty)


def replay(movements: list[tuple[int, int, str | None, int | None]], line_costs: dict[int, int | None],
           voided_lines: set[int]) -> int:
    """movements: (qty_base, unit_cost, ref_type, ref_id) in order. Returns the final avg cost."""
    qty, avg = 0, 0
    for qty_base, unit_cost, ref_type, ref_id in movements:
        if ref_type == REF_PURCHASE_LINE and ref_id not in voided_lines:
            cost = line_costs.get(ref_id)
            if cost is not None and qty_base > 0:
                avg = moving_average(qty, avg, qty_base, cost)
        elif ref_type == REF_OPENING:
            avg = unit_cost
        qty += qty_base
    return avg


def recompute_avg_cost(db: Session, product_id: int) -> int:
    """Replay all movements of one product and store the result in products.avg_cost."""
    movements = db.execute(
        select(StockMovement.qty_base, StockMovement.unit_cost, StockMovement.ref_type, StockMovement.ref_id)
        .where(StockMovement.product_id == product_id)
        .order_by(StockMovement.id)
    ).all()
    line_ids = [m.ref_id for m in movements if m.ref_type == REF_PURCHASE_LINE]
    line_costs: dict[int, int | None] = {}
    voided: set[int] = set()
    if line_ids:
        for line_id, cost_base, status in db.execute(
            select(PurchaseLine.id, PurchaseLine.cost_base, Purchase.status)
            .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
            .where(PurchaseLine.id.in_(line_ids))
        ):
            line_costs[line_id] = cost_base
            if status == PURCHASE_VOIDED:
                voided.add(line_id)
    avg = replay([tuple(m) for m in movements], line_costs, voided)
    product = db.get(Product, product_id)
    product.avg_cost = avg
    db.flush()
    return avg
