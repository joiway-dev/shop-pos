"""Multi-line order parsing, e.g. text pasted from LINE (SPEC 4.4).

For each line: pull out quantity + unit, match the rest to a product, then
check the unit exists for that product.

Quantity rules (in order):
  1. a number directly followed by a sale unit ("10 ถุง", "10ถุง") -- the LAST
     such pair wins, so "เหล็ก 12 มิล 20 เส้น" -> 20 เส้น, name "เหล็ก 12 มิล".
     A number followed by a size unit that is not a sale unit ("12 มิล") is
     part of the product name.
  2. "x10" / "×10"
  3. a bare number at the end of the line ("ปูนเสือ 10") -> quantity in the
     default sale unit, flagged for review (owner decision)
  4. otherwise quantity 1, flagged for review
"""

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Product, ProductUnit
from app.services.matching import matcher
from app.services.matching.matcher import MatchCandidate
from app.services.matching.normalize import THAI_DIGITS, UNIT_SYNONYMS, canonical_unit, normalize
from app.services.money import AmountError, parse_qty

MAX_LINES = 200

STATUS_OK = "ok"
STATUS_NEEDS_CHOICE = "needs_choice"
STATUS_NOT_FOUND = "not_found"
STATUS_SKIPPED = "skipped"

STATUS_LABELS = {
    STATUS_OK: "✓",
    STATUS_NEEDS_CHOICE: "ต้องเลือก",
    STATUS_NOT_FOUND: "ไม่พบ",
    STATUS_SKIPPED: "ข้าม",
}

_SIZE_UNITS = set(UNIT_SYNONYMS)
# Thai marks that cannot start a new syllable: splitting a word right before
# one of these would cut a syllable in half ("มิล" must not yield unit "ม").
_NO_SPLIT_BEFORE = set("ะาำๅ") | {chr(c) for c in range(0x0E31, 0x0E32)} | {
    chr(c) for c in range(0x0E34, 0x0E3B)
} | {chr(c) for c in range(0x0E47, 0x0E4F)}

_ZERO_WIDTH_RE = re.compile("[​-‍⁠﻿]")
_BULLET_RE = re.compile(r"^\s*(?:[-•*·]+|\d{1,3}[.)])\s+")
_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?!\d)")
_TIMES_QTY_RE = re.compile(r"(?:^|\s)[x×*]\s*(\d+(?:\.\d+)?)(?=\s|$)")
_TRAILING_QTY_RE = re.compile(r"\s(\d+(?:\.\d+)?)\s*$")
_LEADING_WORDS_RE = re.compile(r"^(?:ขอ|เอา|สั่ง)\s*")
_TRAILING_WORDS_RE = re.compile(r"(?:\s*(?:ค่ะ|คะ|ครับ|คับ|นะ|จ้า|จ้ะ|ด้วย|หน่อย|เลย))+\s*$")
_WORD_END_RE = re.compile(r"[\s\d]")


@dataclass
class UnitChoice:
    unit_id: int
    unit_name: str
    price: int


@dataclass
class ParsedLine:
    line_no: int
    raw: str
    product_text: str = ""
    status: str = STATUS_NOT_FOUND
    qty: int = 1000  # x1000
    unit_text: str | None = None
    review_note: str | None = None  # quantity/unit was assumed -> highlight
    product_id: int | None = None
    product_name: str | None = None
    unit_id: int | None = None
    unit_name: str | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    unit_choices: list[UnitChoice] = field(default_factory=list)
    message: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


def _prepare(line: str) -> str:
    line = unicodedata.normalize("NFC", line)
    line = _ZERO_WIDTH_RE.sub("", line).translate(THAI_DIGITS).lower()
    return _BULLET_RE.sub("", line).strip()


def sale_units(db: Session) -> set[str]:
    return {canonical_unit(n) for n in db.scalars(select(ProductUnit.unit_name).distinct())}


def _unit_prefix(after: str, units: set[str]) -> str | None:
    """The unit word at the start of `after`, if it is a sale unit."""
    after = after.lstrip()
    end = _WORD_END_RE.search(after)
    word = after[: end.start()] if end else after
    if not word:
        return None
    if canonical_unit(word) in units:
        return word
    if canonical_unit(word) in _SIZE_UNITS:
        return None  # a size such as "12 มิล" belongs to the product name
    # Thai has no spaces: "10ถุงค่ะ" -> try "ถุงค่ะ", "ถุงค่", ..., "ถุง"
    for length in range(len(word) - 1, 0, -1):
        if word[length] in _NO_SPLIT_BEFORE:
            continue
        prefix = word[:length]
        if canonical_unit(prefix) in units:
            return prefix
    return None


def extract_quantity(line: str, units: set[str]) -> tuple[str, str | None, str | None, str | None]:
    """Split a prepared line into (product_text, qty_text, unit_text, review_note)."""
    for m in reversed(list(_NUMBER_RE.finditer(line))):
        after = line[m.end():]
        unit = _unit_prefix(after, units)
        if unit is not None:
            unit_end = m.end() + (len(after) - len(after.lstrip())) + len(unit)
            rest = (line[: m.start()] + " " + line[unit_end:]).strip()
            return rest, m.group(0), unit, None

    m = _TIMES_QTY_RE.search(line)
    if m:
        rest = (line[: m.start()] + " " + line[m.end():]).strip()
        return rest, m.group(1), None, None

    m = _TRAILING_QTY_RE.search(line)
    if m and line[: m.start()].strip():
        return line[: m.start()].strip(), m.group(1), None, "ไม่ได้ระบุหน่วย — ใช้หน่วยขายหลัก"

    return line, None, None, "ไม่ได้ระบุจำนวน — ใส่ 1 ไว้ก่อน"


def _clean_product_text(text: str) -> str:
    text = _TRAILING_WORDS_RE.sub("", text)
    return _LEADING_WORDS_RE.sub("", text).strip()


def _load_product(db: Session, product_id: int) -> Product | None:
    return db.scalar(select(Product).where(Product.id == product_id).options(selectinload(Product.units)))


def _set_unit(line: ParsedLine, product: Product, unit_id: int | None = None) -> None:
    """Pick the unit for a line whose product is known; may leave it needing a choice."""
    line.product_id = product.id
    line.product_name = product.name
    line.unit_choices = [UnitChoice(u.id, u.unit_name, u.price) for u in product.units]
    unit = None
    if unit_id is not None:
        unit = next((u for u in product.units if u.id == unit_id), None)
    elif line.unit_text is None:
        unit = product.default_unit
    else:
        wanted = canonical_unit(line.unit_text)
        unit = next((u for u in product.units if canonical_unit(u.unit_name) == wanted), None)
    if unit is None:
        line.status = STATUS_NEEDS_CHOICE
        line.unit_id = line.unit_name = None
        line.message = f"สินค้านี้ไม่มีหน่วย \"{line.unit_text}\" — เลือกหน่วย"
        return
    line.unit_id = unit.id
    line.unit_name = unit.unit_name
    line.status = STATUS_OK
    line.message = ""


def parse_line(db: Session, line_no: int, raw: str, units: set[str], index, config) -> ParsedLine | None:
    prepared = _prepare(raw)
    if not prepared:
        return None
    result = ParsedLine(line_no=line_no, raw=raw.strip())

    # A scanned barcode on its own line: quantity 1 of that product/unit.
    if result.raw in index.barcodes:
        product_id, unit_id = index.barcodes[result.raw]
        result.product_text = result.raw
        _set_unit(result, _load_product(db, product_id), unit_id=unit_id)
        return result

    product_text, qty_text, unit_text, note = extract_quantity(prepared, units)
    result.product_text = _clean_product_text(product_text)
    result.unit_text = unit_text
    result.review_note = note
    if qty_text is not None:
        try:
            result.qty = parse_qty(qty_text)
        except AmountError:
            result.qty = 1000
            result.review_note = f"จำนวน \"{qty_text}\" ไม่ถูกต้อง — ใส่ 1 ไว้ก่อน"

    has_digit = any(ch.isdigit() for ch in prepared)
    if not normalize(result.product_text):
        result.status = STATUS_NOT_FOUND if has_digit else STATUS_SKIPPED
        result.message = "ไม่มีชื่อสินค้า" if has_digit else "ไม่ใช่รายการสินค้า"
        return result

    match = matcher.decide(result.product_text, matcher.rank(index, result.product_text, config), config)
    result.candidates = match.candidates
    if match.status == matcher.STATUS_NOT_FOUND:
        if has_digit:
            result.status, result.message = STATUS_NOT_FOUND, "ไม่พบสินค้า — ค้นหาเองหรือข้าม"
        else:
            result.status, result.message = STATUS_SKIPPED, "ไม่ใช่รายการสินค้า"
    elif match.status == matcher.STATUS_NEEDS_CHOICE:
        result.status, result.message = STATUS_NEEDS_CHOICE, "มีสินค้าใกล้เคียงหลายตัว — เลือกสินค้า"
    else:
        best = match.best
        product = _load_product(db, best.product_id)
        _set_unit(result, product, unit_id=best.unit_id if unit_text is None else None)
    return result


def parse_order(db: Session, text: str) -> list[ParsedLine]:
    units = sale_units(db)
    index = matcher.get_index(db)
    config = matcher.get_config(db)
    lines = []
    for line_no, raw in enumerate((text or "").splitlines()[:MAX_LINES], start=1):
        parsed = parse_line(db, line_no, raw, units, index, config)
        if parsed is not None:
            lines.append(parsed)
    return lines


def choose(db: Session, line: ParsedLine, product_id: int, unit_id: int | None = None) -> ParsedLine:
    """Apply a staff choice of product (and optionally unit) to a line."""
    product = _load_product(db, product_id)
    if product is None or not product.is_active:
        line.status, line.message = STATUS_NEEDS_CHOICE, "ไม่พบสินค้าที่เลือก"
        return line
    _set_unit(line, product, unit_id)
    return line


def ready_to_bill(lines: list[ParsedLine]) -> bool:
    """The bill button is enabled only when every line is ✓ or skipped."""
    return any(l.status == STATUS_OK for l in lines) and all(
        l.status in (STATUS_OK, STATUS_SKIPPED) for l in lines
    )
