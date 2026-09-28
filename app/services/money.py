"""Parsing and formatting of money (satang) and quantities (x1000).

Never uses float: text -> Decimal -> int.
"""

from decimal import Decimal, InvalidOperation

from app.models.catalog import QTY_SCALE

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


class AmountError(ValueError):
    """Raised with a Thai message that can be shown to the user."""


def _to_decimal(text: str | int | Decimal, what: str) -> Decimal:
    if isinstance(text, (int, Decimal)):
        value = Decimal(text)
    else:
        cleaned = str(text).translate(THAI_DIGITS).replace(",", "").strip()
        if not cleaned:
            raise AmountError(f"กรุณากรอก{what}")
        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            raise AmountError(f"{what}ต้องเป็นตัวเลข") from None
    if not value.is_finite():
        raise AmountError(f"{what}ต้องเป็นตัวเลข")
    return value


def _scale(value: Decimal, places: int, what: str) -> int:
    quant = Decimal(1).scaleb(-places)
    if value != value.quantize(quant):
        raise AmountError(f"{what}มีทศนิยมได้ไม่เกิน {places} ตำแหน่ง")
    return int(value.scaleb(places))


def parse_money(text: str | int | Decimal, what: str = "ราคา") -> int:
    """'1,250.50' -> 125050 satang. Must be >= 0 with at most 2 decimals."""
    value = _to_decimal(text, what)
    if value < 0:
        raise AmountError(f"{what}ต้องไม่ติดลบ")
    return _scale(value, 2, what)


def parse_qty(text: str | int | Decimal, what: str = "จำนวน", allow_zero: bool = False) -> int:
    """'2.5' -> 2500. Must be > 0 (or >= 0) with at most 3 decimals."""
    value = _to_decimal(text, what)
    if value < 0 or (value == 0 and not allow_zero):
        raise AmountError(f"{what}ต้องมากกว่า 0" if not allow_zero else f"{what}ต้องไม่ติดลบ")
    return _scale(value, 3, what)


def format_money(satang: int | None) -> str:
    """125050 -> '1,250.50'"""
    if satang is None:
        return ""
    sign = "-" if satang < 0 else ""
    baht, cents = divmod(abs(satang), 100)
    return f"{sign}{baht:,}.{cents:02d}"


def format_money_input(satang: int | None) -> str:
    """125050 -> '1250.50' (no thousands separator, for form inputs)."""
    if satang is None:
        return ""
    return format_money(satang).replace(",", "")


def format_qty(milli: int | None) -> str:
    """2500 -> '2.5', 40000 -> '40', 1234567 -> '1,234.567'"""
    if milli is None:
        return ""
    sign = "-" if milli < 0 else ""
    whole, frac = divmod(abs(milli), QTY_SCALE)
    text = f"{sign}{whole:,}"
    if frac:
        text += "." + f"{frac:03d}".rstrip("0")
    return text


def format_qty_input(milli: int | None) -> str:
    return format_qty(milli).replace(",", "")
