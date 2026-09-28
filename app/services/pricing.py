"""Bill arithmetic: line totals, discounts and VAT (pure functions, no DB).

All amounts are satang (int). Rules:
- line gross = qty x unit price, rounded half-up to the satang
- line total = gross - line discount
- the bill discount is split between VAT-able and VAT-exempt lines in
  proportion to their totals
- VAT is computed ONCE on the bill's VAT-able amount (never per line):
    prices include VAT:  vat = round(amount x r / (100 + r)), total unchanged
    prices exclude VAT:  vat = round(amount x r / 100), added on top
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.models.catalog import QTY_SCALE
from app.services.money import AmountError, parse_money

VAT_MODE_NONE = "none"


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_gross(qty: int, unit_price: int) -> int:
    """qty is x1000, unit_price satang -> satang."""
    return round_half_up(Decimal(qty) * Decimal(unit_price) / QTY_SCALE)


def base_qty(qty: int, factor_to_base: int) -> int:
    """Quantity in the sale unit (x1000) -> base units (x1000)."""
    return round_half_up(Decimal(qty) * Decimal(factor_to_base) / QTY_SCALE)


def parse_discount(text: str | None, base: int) -> int:
    """'50' -> 5000 satang; '5%' -> 5% of `base`. Must not exceed `base`."""
    text = (text or "").strip()
    if not text or text in ("0", "0%"):
        return 0
    if text.endswith("%"):
        try:
            pct = Decimal(text[:-1].strip())
        except Exception:
            raise AmountError("ส่วนลดต้องเป็นตัวเลข เช่น 50 หรือ 5%") from None
        if not pct.is_finite() or pct < 0 or pct > 100:
            raise AmountError("ส่วนลดเป็นเปอร์เซ็นต์ต้องอยู่ระหว่าง 0–100")
        amount = round_half_up(Decimal(base) * pct / 100)
    else:
        amount = parse_money(text, "ส่วนลด")
    if amount > base:
        raise AmountError("ส่วนลดมากกว่ายอดขาย")
    return amount


@dataclass(frozen=True)
class LineInput:
    qty: int  # x1000
    unit_price: int
    discount: int = 0
    vat_exempt: bool = False


@dataclass(frozen=True)
class BillTotals:
    line_totals: list[int]
    subtotal: int  # sum of line totals (after line discounts)
    discount: int  # bill discount
    vatable_amount: int  # VAT base, excluding VAT (0 when not VAT registered)
    exempt_amount: int  # VAT-exempt sales (0 when not VAT registered)
    vat_amount: int
    total: int  # amount to pay


def compute_bill(
    lines: list[LineInput],
    bill_discount: int,
    vat_mode: str,
    vat_rate_bp: int,
    price_includes_vat: bool,
) -> BillTotals:
    line_totals = []
    for line in lines:
        gross = line_gross(line.qty, line.unit_price)
        if line.discount < 0 or line.discount > gross:
            raise AmountError("ส่วนลดต่อบรรทัดต้องไม่เกินยอดของบรรทัดนั้น")
        line_totals.append(gross - line.discount)
    subtotal = sum(line_totals)
    if bill_discount < 0 or bill_discount > subtotal:
        raise AmountError("ส่วนลดท้ายบิลต้องไม่เกินยอดรวม")
    net = subtotal - bill_discount

    if vat_mode == VAT_MODE_NONE:
        return BillTotals(line_totals, subtotal, bill_discount, 0, 0, 0, net)

    vatable_gross = sum(t for t, l in zip(line_totals, lines) if not l.vat_exempt)
    discount_on_vatable = (
        round_half_up(Decimal(bill_discount) * vatable_gross / subtotal) if subtotal else 0
    )
    vatable_net = vatable_gross - discount_on_vatable
    exempt_net = net - vatable_net

    rate = Decimal(vat_rate_bp)  # basis points: 700 = 7%
    if price_includes_vat:
        vat = round_half_up(Decimal(vatable_net) * rate / (10000 + rate))
        return BillTotals(line_totals, subtotal, bill_discount, vatable_net - vat, exempt_net, vat, net)
    vat = round_half_up(Decimal(vatable_net) * rate / 10000)
    return BillTotals(line_totals, subtotal, bill_discount, vatable_net, exempt_net, vat, net + vat)
