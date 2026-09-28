"""Point-of-sale: quote a cart, save a sale, void a sale, record prints.

Prices always come from the database; the browser only sends product, unit,
quantity and discount text.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import begin_immediate
from app.models import Customer, Product, ProductUnit, Quotation, Sale, SaleLine, ShopSetting, User
from app.models.customers import QT_CONVERTED
from app.models.base import utcnow
from app.models.sales import MOVE_SALE, MOVE_SALE_VOID, PAY_CASH, PAY_CREDIT, PAY_TRANSFER, SALE_COMPLETED, SALE_VOIDED
from app.models.settings import HEAD_OFFICE_BRANCH_NO, VAT_MODE_VAT
from app.services import auth, documents, receivables, stock
from app.services.customers import buyer_from_customer
from app.services.audit import log_action
from app.services.clock import now_local
from app.services.money import AmountError, format_money, format_qty, parse_money, parse_qty
from app.services.pricing import BillTotals, LineInput, base_qty, compute_bill, line_gross, parse_discount
from app.services.settings import get_shop_settings, is_valid_thai_tax_id, shop_snapshot

MAX_LINES = 200


class SaleError(ValueError):
    """User-facing problem with a sale. `pin_failed` marks a wrong/missing owner PIN."""

    def __init__(self, message: str, pin_failed: bool = False):
        super().__init__(message)
        self.pin_failed = pin_failed


# --- inputs --------------------------------------------------------------------


@dataclass
class CartLineInput:
    product_id: int
    unit_id: int
    qty: str | int  # text typed by staff, or already x1000 when int
    discount: str = ""


@dataclass
class BuyerInput:
    name: str = ""
    address: str = ""
    tax_id: str = ""
    branch: str = ""  # "" / "สำนักงานใหญ่" / branch number

    @property
    def given(self) -> bool:
        return bool(self.name.strip())


@dataclass
class CheckoutInput:
    lines: list[CartLineInput]
    bill_discount: str = ""
    payment_type: str = PAY_CASH
    cash_received: str = ""
    buyer: BuyerInput | None = None
    owner_pin: str = ""
    replaces_sale_id: int | None = None
    customer_id: int | None = None
    quotation_id: int | None = None  # sell exactly what the quotation says, at its prices


# --- quote ---------------------------------------------------------------------


@dataclass
class UnitOption:
    unit_id: int
    unit_name: str
    price: int


@dataclass
class QuoteLine:
    product_id: int
    unit_id: int
    sku: str = ""
    name: str = ""
    unit_name: str = ""
    factor_to_base: int = 1000
    qty: int = 0
    unit_price: int = 0
    discount: int = 0
    line_total: int = 0
    vat_exempt: bool = False
    avg_cost: int = 0
    units: list[UnitOption] = field(default_factory=list)
    error: str | None = None
    stock_warning: str | None = None


@dataclass
class Quote:
    lines: list[QuoteLine]
    totals: BillTotals | None
    errors: list[str]
    has_discount: bool

    @property
    def ok(self) -> bool:
        return not self.errors and all(l.error is None for l in self.lines) and self.totals is not None


def _to_qty(value: str | int) -> int:
    if isinstance(value, int):
        if value <= 0:
            raise AmountError("จำนวนต้องมากกว่า 0")
        return value
    return parse_qty(value)


def build_quote(db: Session, lines: list[CartLineInput], bill_discount: str, settings: ShopSetting) -> Quote:
    errors: list[str] = []
    if len(lines) > MAX_LINES:
        errors.append(f"บิลหนึ่งมีได้ไม่เกิน {MAX_LINES} รายการ")
        lines = lines[:MAX_LINES]

    product_ids = {l.product_id for l in lines}
    products = {
        p.id: p
        for p in db.scalars(select(Product).where(Product.id.in_(product_ids)).options(selectinload(Product.units)))
    }
    on_hand = stock.balances(db, list(product_ids))
    base_used: dict[int, int] = {}

    quote_lines: list[QuoteLine] = []
    for line in lines:
        q = QuoteLine(product_id=line.product_id, unit_id=line.unit_id)
        quote_lines.append(q)
        product = products.get(line.product_id)
        if product is None or not product.is_active:
            q.error = "ไม่พบสินค้า หรือสินค้าถูกปิดใช้งาน"
            continue
        q.sku, q.name, q.vat_exempt, q.avg_cost = product.sku, product.name, product.vat_exempt, product.avg_cost
        q.units = [UnitOption(u.id, u.unit_name, u.price) for u in product.units]
        unit = next((u for u in product.units if u.id == line.unit_id), None)
        if unit is None:
            q.error = "ไม่พบหน่วยนี้ในสินค้า"
            continue
        q.unit_name, q.factor_to_base, q.unit_price = unit.unit_name, unit.factor_to_base, unit.price
        try:
            q.qty = _to_qty(line.qty)
            q.discount = parse_discount(line.discount, line_gross(q.qty, q.unit_price))
        except AmountError as e:
            q.error = str(e)
            continue
        used = base_used.get(product.id, 0) + base_qty(q.qty, q.factor_to_base)
        base_used[product.id] = used
        left = on_hand.get(product.id, 0) - used
        if left < 0:
            q.stock_warning = f"สต็อกไม่พอ (มี {format_qty(on_hand.get(product.id, 0))} {product.base_unit})"

    totals = None
    valid = [q for q in quote_lines if q.error is None]
    if valid and len(valid) == len(quote_lines):
        pre = compute_bill([LineInput(q.qty, q.unit_price, q.discount, q.vat_exempt) for q in valid],
                           0, settings.vat_mode, settings.vat_rate_bp, settings.price_includes_vat)
        try:
            bill_disc = parse_discount(bill_discount, pre.subtotal)
            totals = compute_bill(
                [LineInput(q.qty, q.unit_price, q.discount, q.vat_exempt) for q in valid],
                bill_disc, settings.vat_mode, settings.vat_rate_bp, settings.price_includes_vat,
            )
            for q, t in zip(valid, totals.line_totals):
                q.line_total = t
        except AmountError as e:
            errors.append(str(e))
    has_discount = any(q.discount for q in quote_lines) or bool(totals and totals.discount)
    return Quote(quote_lines, totals, errors, has_discount)


def quote(db: Session, lines: list[CartLineInput], bill_discount: str = "") -> Quote:
    return build_quote(db, lines, bill_discount, get_shop_settings(db))


# --- checkout ------------------------------------------------------------------


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _buyer_snapshot(buyer: BuyerInput | None) -> dict | None:
    if buyer is None or not buyer.given:
        return None
    name, address = _clean(buyer.name), (buyer.address or "").strip()
    if not address:
        raise SaleError("กรุณากรอกที่อยู่ผู้ซื้อ")
    tax_id = re.sub(r"[\s-]", "", buyer.tax_id or "")
    if tax_id and not is_valid_thai_tax_id(tax_id):
        raise SaleError("เลขประจำตัวผู้เสียภาษีของผู้ซื้อไม่ถูกต้อง")
    branch = _clean(buyer.branch)
    if branch in ("", "สำนักงานใหญ่", HEAD_OFFICE_BRANCH_NO):
        branch = HEAD_OFFICE_BRANCH_NO if tax_id else ""
    elif re.fullmatch(r"\d{1,5}", branch):
        branch = branch.zfill(5)
    else:
        raise SaleError("สาขาของผู้ซื้อต้องเป็น \"สำนักงานใหญ่\" หรือเลขสาขา")
    return {"name": name[:200], "address": address[:500], "tax_id": tax_id or None, "branch_no": branch or None}


def _open_quotation(db: Session, quotation_id: int) -> Quotation:
    qt = db.scalar(select(Quotation).where(Quotation.id == quotation_id).options(selectinload(Quotation.lines)))
    if qt is None:
        raise SaleError("ไม่พบใบเสนอราคา")
    if qt.status == QT_CONVERTED:
        raise SaleError("ใบเสนอราคานี้แปลงเป็นบิลขายไปแล้ว")
    if qt.is_expired(now_local().date()):
        raise SaleError("ใบเสนอราคาหมดอายุแล้ว — ต่ออายุก่อนแปลงเป็นบิล")
    return qt


def quote_from_quotation(db: Session, qt: Quotation, settings: ShopSetting) -> Quote:
    """Price a quotation's lines at the quoted prices (not today's prices)."""
    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_({l.product_id for l in qt.lines})))
    }
    on_hand = stock.balances(db, list(products))
    used: dict[int, int] = {}
    lines = []
    for ql in qt.lines:
        p = products.get(ql.product_id)
        q = QuoteLine(
            product_id=ql.product_id, unit_id=ql.unit_id or 0, sku=ql.sku_snapshot, name=ql.product_name_snapshot,
            unit_name=ql.unit_name, factor_to_base=ql.factor_to_base, qty=ql.qty, unit_price=ql.unit_price,
            discount=ql.discount, vat_exempt=ql.vat_exempt, avg_cost=p.avg_cost if p else 0,
        )
        if p is None or not p.is_active:
            q.error = "สินค้าถูกปิดใช้งานแล้ว"
        else:
            used[p.id] = used.get(p.id, 0) + base_qty(q.qty, q.factor_to_base)
            if on_hand.get(p.id, 0) - used[p.id] < 0:
                q.stock_warning = f"สต็อกไม่พอ (มี {format_qty(on_hand.get(p.id, 0))} {p.base_unit})"
        lines.append(q)
    totals = compute_bill([LineInput(l.qty, l.unit_price, l.discount, l.vat_exempt) for l in lines],
                          qt.discount, settings.vat_mode, settings.vat_rate_bp, settings.price_includes_vat)
    for l, t in zip(lines, totals.line_totals):
        l.line_total = t
    return Quote(lines, totals, [], has_discount=False)  # discounts were approved when quoting


def create_sale(db: Session, actor: User, data: CheckoutInput) -> Sale:
    """Save a sale with its document number and stock movements (caller commits).

    On SaleError with pin_failed=True the caller should still commit so the
    failed-PIN counter is kept (nothing else has been written at that point).
    """
    begin_immediate(db)
    settings = get_shop_settings(db)
    quotation = None
    if data.quotation_id:
        quotation = _open_quotation(db, data.quotation_id)
        q = quote_from_quotation(db, quotation, settings)
    else:
        if not data.lines:
            raise SaleError("ยังไม่มีสินค้าในบิล")
        q = build_quote(db, data.lines, data.bill_discount, settings)
    if not q.ok:
        problems = q.errors + [f"{l.name or 'รายการ'}: {l.error}" for l in q.lines if l.error]
        raise SaleError(problems[0])
    totals = q.totals
    is_vat = settings.vat_mode == VAT_MODE_VAT

    customer = None
    customer_id = data.customer_id or (quotation.customer_id if quotation else None)
    if customer_id:
        customer = db.get(Customer, customer_id)
        if customer is None or not customer.is_active:
            raise SaleError("ไม่พบลูกค้า หรือลูกค้าถูกปิดใช้งาน")

    if data.payment_type not in (PAY_CASH, PAY_TRANSFER, PAY_CREDIT):
        raise SaleError("วิธีชำระเงินไม่ถูกต้อง")
    credit = data.payment_type == PAY_CREDIT
    over_limit = False
    if credit:
        if customer is None:
            raise SaleError("ขายเงินเชื่อต้องเลือกลูกค้า")
        over_limit = receivables.customer_balance(db, customer.id) + totals.total > customer.credit_limit

    needs = [text for flag, text in ((q.has_discount, "การให้ส่วนลด"), (over_limit, "การขายเชื่อเกินวงเงิน")) if flag]
    approver = None
    if needs and not actor.is_owner:
        approver = auth.verify_owner_pin(db, data.owner_pin or "")
        if approver is None:
            raise SaleError(f"{' และ '.join(needs)} ต้องใช้ PIN เจ้าของร้าน (PIN ไม่ถูกต้อง)", pin_failed=True)

    cash_received = change = None
    if data.payment_type == PAY_CASH:
        try:
            cash_received = parse_money(data.cash_received or format_money(totals.total).replace(",", ""), "เงินที่รับ")
        except AmountError as e:
            raise SaleError(str(e)) from None
        if cash_received < totals.total:
            raise SaleError("รับเงินไม่พอ")
        change = cash_received - totals.total

    if data.buyer is not None and data.buyer.given:
        buyer = _buyer_snapshot(data.buyer)
    else:
        buyer = buyer_from_customer(customer) if customer else None
    if buyer is not None and is_vat and not (buyer.get("address") or "").strip():
        raise SaleError("กรุณาใส่ที่อยู่ลูกค้า (ใช้พิมพ์ในใบกำกับภาษี)")
    try:
        doc_type = documents.sale_doc_type(settings, buyer is not None, credit=credit)
    except documents.DocumentError as e:
        raise SaleError(str(e)) from None

    replaces = None
    if data.replaces_sale_id:
        replaces = db.get(Sale, data.replaces_sale_id)
        if replaces is None or replaces.status != SALE_VOIDED or replaces.replaced_by_sale_id:
            raise SaleError("บิลเดิมต้องถูกยกเลิกก่อน และยังไม่เคยออกใบใหม่แทน")

    now = now_local()
    doc_no = documents.next_doc_no(
        db, doc_type, now, settings.doc_number_reset, documents.prefix_for(settings, doc_type)
    )
    sale = Sale(
        doc_type=doc_type, doc_no=doc_no, status=SALE_COMPLETED, customer_id=customer.id if customer else None,
        due_date=now.date() + timedelta(days=customer.credit_days) if credit else None,
        subtotal=totals.subtotal, discount=totals.discount, vatable_amount=totals.vatable_amount,
        exempt_amount=totals.exempt_amount, vat_amount=totals.vat_amount, total=totals.total,
        payment_type=data.payment_type, cash_received=cash_received, change_amount=change,
        shop_snapshot=shop_snapshot(settings), buyer_snapshot=buyer,
        vat_rate_snapshot=settings.vat_rate_bp if is_vat else 0, print_count=0, user_id=actor.id,
    )
    for l in q.lines:
        sale.lines.append(SaleLine(
            product_id=l.product_id, sku_snapshot=l.sku, product_name_snapshot=l.name, unit_name=l.unit_name,
            factor_to_base=l.factor_to_base, qty=l.qty, unit_price=l.unit_price, discount=l.discount,
            line_total=l.line_total, vat_exempt=l.vat_exempt, unit_cost_snapshot=l.avg_cost,
        ))
    db.add(sale)
    db.flush()
    for l in sale.lines:
        stock.record_movement(db, l.product_id, -base_qty(l.qty, l.factor_to_base), MOVE_SALE, actor.id,
                              ref_type="sale", ref_id=sale.id, unit_cost=l.unit_cost_snapshot)
    if replaces is not None:
        replaces.replaced_by_sale_id = sale.id
    if quotation is not None:
        quotation.status = QT_CONVERTED
        quotation.converted_sale_id = sale.id
    log_action(db, actor.id, "sale_create", "sale", sale.id, {
        "doc_no": doc_no, "total": format_money(sale.total), "payment": sale.payment_type,
        "customer": customer.name if customer else None, "over_credit_limit": over_limit,
        "approved_by": approver.id if approver else None,
        "replaces": replaces.doc_no if replaces else None,
        "quotation": quotation.doc_no if quotation else None,
    })
    return sale


# --- void / print --------------------------------------------------------------


def void_sale(db: Session, actor: User, sale: Sale, reason: str, owner_pin: str) -> Sale:
    """Void with owner PIN + reason; stock comes back via sale_void movements.
    On SaleError(pin_failed=True) the caller should commit (failed-PIN counter)."""
    reason = _clean(reason)
    if sale.status == SALE_VOIDED:
        raise SaleError("บิลนี้ถูกยกเลิกไปแล้ว")
    if len(reason) < 3:
        raise SaleError("กรุณาระบุเหตุผลที่ยกเลิก")
    if receivables.sale_paid(db, sale.id):
        raise SaleError("บิลนี้มีการรับชำระแล้ว — ยกเลิกใบรับเงินที่ตัดบิลนี้ก่อน")
    approver = auth.verify_owner_pin(db, owner_pin or "")
    if approver is None:
        raise SaleError("PIN เจ้าของร้านไม่ถูกต้อง", pin_failed=True)
    sale.status = SALE_VOIDED
    sale.void_reason = reason[:500]
    sale.voided_by = approver.id
    sale.voided_at = utcnow()
    for l in sale.lines:
        stock.record_movement(db, l.product_id, base_qty(l.qty, l.factor_to_base), MOVE_SALE_VOID, actor.id,
                              ref_type="sale", ref_id=sale.id, unit_cost=l.unit_cost_snapshot)
    log_action(db, actor.id, "sale_void", "sale", sale.id,
               {"doc_no": sale.doc_no, "reason": sale.void_reason, "approved_by": approver.id})
    return sale


def record_print(db: Session, actor: User, sale: Sale, fmt: str) -> bool:
    """Count a print; returns True when this is a reprint (stamp "สำเนา")."""
    is_copy = sale.print_count > 0
    sale.print_count += 1
    if is_copy:
        log_action(db, actor.id, "sale_reprint", "sale", sale.id, {"doc_no": sale.doc_no, "format": fmt})
    return is_copy


def get_sale(db: Session, sale_id: int) -> Sale | None:
    return db.scalar(select(Sale).where(Sale.id == sale_id).options(selectinload(Sale.lines)))


def list_sales(
    db: Session, q: str = "", day: date | None = None, status: str = "", limit: int = 50, offset: int = 0
) -> tuple[list[Sale], int]:
    stmt = select(Sale)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Sale.doc_no.ilike(term), Sale.id.in_(
            select(SaleLine.sale_id).where(SaleLine.product_name_snapshot.ilike(term))
        )))
    if day is not None:
        # The day is local time; stored timestamps are UTC.
        start_utc = datetime.combine(day, time.min).astimezone().astimezone(UTC).replace(tzinfo=None)
        stmt = stmt.where(Sale.created_at >= start_utc, Sale.created_at < start_utc + timedelta(days=1))
    if status in (SALE_COMPLETED, SALE_VOIDED):
        stmt = stmt.where(Sale.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Sale.id.desc()).limit(limit).offset(offset))
    return list(rows), total
