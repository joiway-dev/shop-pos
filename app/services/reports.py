"""Reports (SPEC phase 5). Voided sales never count.

Per-line amounts: a sale's bill discount and VAT are spread over its lines in
proportion to the line totals (largest remainder, to the satang) so that line
figures always add up exactly to the sale's own totals.
  gross = amount the customer paid for the line (incl. VAT)
  net   = gross without VAT            (the base for gross profit)
  cost  = base qty x unit_cost_snapshot (avg cost when sold)
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import ArPayment, Category, Product, Sale, SaleLine, User
from app.models.customers import PAYMENT_COMPLETED
from app.models.sales import PAY_CASH, PAY_CREDIT, PAY_TRANSFER, SALE_COMPLETED, SALE_VOIDED
from app.services import stock
from app.services.clock import local_range_to_utc, to_local
from app.services.pricing import base_qty, round_half_up


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split `total` in proportion to `weights`; the parts always sum to `total`."""
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum == 0:
        parts = [0] * len(weights)
        parts[0] = total
        return parts
    exact = [Decimal(total) * w / weight_sum for w in weights]
    parts = [int(x) for x in exact]  # truncate toward zero
    rest = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: exact[i] - parts[i], reverse=total >= 0)
    step = 1 if rest >= 0 else -1
    for i in order[: abs(rest)]:
        parts[i] += step
    return parts


@dataclass
class LineFigures:
    line: SaleLine
    sale: Sale
    gross: int
    net: int
    cost: int
    base_qty: int

    @property
    def profit(self) -> int:
        return self.net - self.cost


def _sales(db: Session, start: date, end: date, status: str | None = SALE_COMPLETED) -> list[Sale]:
    lo, hi = local_range_to_utc(start, end)
    stmt = select(Sale).where(Sale.created_at >= lo, Sale.created_at < hi).options(selectinload(Sale.lines))
    if status:
        stmt = stmt.where(Sale.status == status)
    return list(db.scalars(stmt.order_by(Sale.id)))


def line_figures(db: Session, start: date, end: date) -> list[LineFigures]:
    out = []
    for sale in _sales(db, start, end):
        weights = [l.line_total for l in sale.lines]
        gross = allocate(sale.total, weights)
        net = allocate(sale.total - sale.vat_amount, weights)
        for l, g, n in zip(sale.lines, gross, net):
            bq = base_qty(l.qty, l.factor_to_base)
            cost = round_half_up(Decimal(bq) * l.unit_cost_snapshot / 1000)
            out.append(LineFigures(l, sale, g, n, cost, bq))
    return out


# --- sales by day / month --------------------------------------------------------


@dataclass
class PeriodRow:
    key: str  # "2026-09-28" or "2026-09"
    label: str
    bills: int = 0
    gross: int = 0
    discount: int = 0
    vat: int = 0
    cash: int = 0
    transfer: int = 0
    credit: int = 0
    voided: int = 0

    @property
    def net(self) -> int:
        return self.gross - self.vat


def sales_by_period(db: Session, start: date, end: date, group: str = "day") -> list[PeriodRow]:
    rows: dict[str, PeriodRow] = {}

    def row_for(sale: Sale) -> PeriodRow:
        d = to_local(sale.created_at).date()
        if group == "month":
            key, label = f"{d.year}-{d.month:02d}", f"{d.month:02d}/{d.year + 543}"
        else:
            key, label = d.isoformat(), f"{d.day:02d}/{d.month:02d}/{d.year + 543}"
        return rows.setdefault(key, PeriodRow(key, label))

    for sale in _sales(db, start, end, status=None):
        r = row_for(sale)
        if sale.status == SALE_VOIDED:
            r.voided += 1
            continue
        r.bills += 1
        r.gross += sale.total
        r.discount += sale.discount + sum(l.discount for l in sale.lines)
        r.vat += sale.vat_amount
        if sale.payment_type == PAY_CASH:
            r.cash += sale.total
        elif sale.payment_type == PAY_TRANSFER:
            r.transfer += sale.total
        elif sale.payment_type == PAY_CREDIT:
            r.credit += sale.total
    return [rows[k] for k in sorted(rows)]


def total_row(rows: list[PeriodRow]) -> PeriodRow:
    t = PeriodRow("total", "รวม")
    for r in rows:
        for f in ("bills", "gross", "discount", "vat", "cash", "transfer", "credit", "voided"):
            setattr(t, f, getattr(t, f) + getattr(r, f))
    return t


# --- breakdowns ----------------------------------------------------------------------


@dataclass
class GroupRow:
    key: object
    label: str
    qty: int = 0  # base units (products only)
    unit: str = ""
    bills: set = field(default_factory=set)
    gross: int = 0
    net: int = 0
    cost: int = 0

    @property
    def profit(self) -> int:
        return self.net - self.cost

    @property
    def margin_pct(self) -> Decimal | None:
        return (Decimal(self.profit) * 100 / self.net).quantize(Decimal("0.1")) if self.net else None

    @property
    def bill_count(self) -> int:
        return len(self.bills)


def breakdown(db: Session, start: date, end: date, by: str = "product") -> list[GroupRow]:
    """by: product | category | cashier. Sorted by sales, highest first."""
    figures = line_figures(db, start, end)
    products = {p.id: p for p in db.scalars(select(Product).where(
        Product.id.in_({f.line.product_id for f in figures})))} if figures else {}
    categories = {c.id: c.name for c in db.scalars(select(Category))}
    users = {u.id: u.name for u in db.scalars(select(User))}
    rows: dict[object, GroupRow] = {}
    for f in figures:
        p = products.get(f.line.product_id)
        if by == "category":
            cid = p.category_id if p else None
            key, label = cid, categories.get(cid, "ไม่ระบุหมวด")
        elif by == "cashier":
            key, label = f.sale.user_id, users.get(f.sale.user_id, "-")
        else:
            key, label = f.line.product_id, f.line.product_name_snapshot
        r = rows.setdefault(key, GroupRow(key, label, unit=p.base_unit if (p and by == "product") else ""))
        if by == "product":
            r.qty += f.base_qty
        r.bills.add(f.sale.id)
        r.gross += f.gross
        r.net += f.net
        r.cost += f.cost
    return sorted(rows.values(), key=lambda r: (-r.gross, str(r.label)))


def group_total(rows: list[GroupRow]) -> GroupRow:
    t = GroupRow("total", "รวม")
    for r in rows:
        t.bills |= r.bills
        t.gross += r.gross
        t.net += r.net
        t.cost += r.cost
    return t


def top_sellers(db: Session, start: date, end: date, limit: int = 20, by: str = "gross") -> list[GroupRow]:
    rows = breakdown(db, start, end, "product")
    key = (lambda r: -r.qty) if by == "qty" else (lambda r: -r.gross)
    return sorted(rows, key=key)[:limit]


@dataclass
class IdleRow:
    product: Product
    balance: int
    last_sold: date | None


def non_moving(db: Session, days: int = 60, today: date | None = None) -> list[IdleRow]:
    """Active products not sold in the last `days` days (oldest sale first)."""
    today = today or date.today()
    since, _ = local_range_to_utc(today - timedelta(days=days - 1), today)
    last = dict(db.execute(
        select(SaleLine.product_id, func.max(Sale.created_at))
        .join(Sale, Sale.id == SaleLine.sale_id)
        .where(Sale.status == SALE_COMPLETED)
        .group_by(SaleLine.product_id)
    ).all())
    balances = stock.balances(db)
    rows = []
    for p in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name)):
        sold = last.get(p.id)
        if sold is None or sold < since:
            rows.append(IdleRow(p, balances.get(p.id, 0), to_local(sold).date() if sold else None))
    return sorted(rows, key=lambda r: (r.last_sold is not None, r.last_sold or date.min, r.product.name))


# --- output tax report ---------------------------------------------------------------


@dataclass
class TaxRow:
    sale: Sale
    issued: date
    buyer_name: str
    tax_id: str
    branch: str
    voided: bool

    @property
    def vatable(self) -> int:
        return 0 if self.voided else self.sale.vatable_amount

    @property
    def exempt(self) -> int:
        return 0 if self.voided else self.sale.exempt_amount

    @property
    def vat(self) -> int:
        return 0 if self.voided else self.sale.vat_amount

    @property
    def total(self) -> int:
        return 0 if self.voided else self.sale.total


def tax_report(db: Session, start: date, end: date) -> list[TaxRow]:
    """รายงานภาษีขาย: every tax invoice issued in the period, by document number.
    Voided documents stay in the list with zero amounts (marked ยกเลิก)."""
    rows = []
    for sale in _sales(db, start, end, status=None):
        if not sale.vat_rate_snapshot:
            continue  # issued while not VAT registered
        buyer = sale.buyer_snapshot or {}
        branch = buyer.get("branch_no") or ""
        rows.append(TaxRow(
            sale=sale, issued=to_local(sale.created_at).date(),
            buyer_name=buyer.get("name") or "ลูกค้าทั่วไป (ขายปลีก)",
            tax_id=buyer.get("tax_id") or "",
            branch="สำนักงานใหญ่" if branch == "00000" else (f"สาขา {branch}" if branch else ""),
            voided=sale.status == SALE_VOIDED,
        ))
    return sorted(rows, key=lambda r: (r.sale.doc_type, r.sale.doc_no))


# --- cash for the daily close -----------------------------------------------------


def day_cash(db: Session, day: date) -> dict[str, int]:
    """Money taken on a local day, by kind (completed sales / payments only)."""
    lo, hi = local_range_to_utc(day, day)
    out = {"cash_sales": 0, "transfer": 0, "credit": 0, "bills": 0, "cash_receipts": 0, "other_receipts": 0}
    for sale in db.scalars(select(Sale).where(Sale.created_at >= lo, Sale.created_at < hi,
                                              Sale.status == SALE_COMPLETED)):
        out["bills"] += 1
        if sale.payment_type == PAY_CASH:
            out["cash_sales"] += sale.total
        elif sale.payment_type == PAY_TRANSFER:
            out["transfer"] += sale.total
        else:
            out["credit"] += sale.total
    for p in db.scalars(select(ArPayment).where(ArPayment.created_at >= lo, ArPayment.created_at < hi,
                                                ArPayment.status == PAYMENT_COMPLETED)):
        if p.method == "cash":
            out["cash_receipts"] += p.amount
        else:
            out["other_receipts"] += p.amount
    return out
