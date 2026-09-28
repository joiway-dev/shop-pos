"""Document types and running numbers (SPEC 3, CLAUDE.md rule 4).

Numbers are issued inside the same transaction that saves the document; the
caller must have started it with `db.begin_immediate` so two tills cannot get
the same number.
"""

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocSequence, ShopSetting
from app.models.settings import VAT_MODE_VAT

DOC_RC = "RC"
DOC_ABB = "ABB"
DOC_TAX = "TAX"
DOC_DN = "DN"
DOC_CN = "CN"
DOC_QT = "QT"
DOC_RV = "RV"
DOC_GR = "GR"

DEFAULT_PREFIXES = {
    DOC_RC: "RC", DOC_ABB: "ABB", DOC_TAX: "INV", DOC_DN: "DN", DOC_CN: "CN", DOC_QT: "QT", DOC_RV: "RV",
    DOC_GR: "GR",
}
_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,6}$")

TITLES = {
    DOC_RC: "ใบเสร็จรับเงิน",
    DOC_ABB: "ใบกำกับภาษีอย่างย่อ/ใบเสร็จรับเงิน",
    DOC_TAX: "ใบกำกับภาษี/ใบเสร็จรับเงิน",
    DOC_DN: "ใบส่งของ/ใบแจ้งหนี้",
    DOC_CN: "ใบลดหนี้",
    DOC_QT: "ใบเสนอราคา",
    DOC_RV: "ใบรับเงิน",
    DOC_GR: "ใบรับสินค้า",
}

# Default paper per document type (SPEC phase 2).
DEFAULT_FORMAT = {DOC_RC: "80mm", DOC_ABB: "80mm", DOC_TAX: "a4", DOC_DN: "a4", DOC_QT: "a4", DOC_CN: "a4", DOC_RV: "a4"}


class DocumentError(ValueError):
    pass


def title_for(doc_type: str, vat_mode: str | None) -> str:
    """Printed title. A VAT-registered shop's credit sale is also its tax invoice
    (tax point = delivery), so the DN title says so (owner decision)."""
    if doc_type == DOC_DN and vat_mode == VAT_MODE_VAT:
        return "ใบส่งของ/ใบแจ้งหนี้/ใบกำกับภาษี"
    return TITLES[doc_type]


def sale_doc_type(settings: ShopSetting, has_buyer: bool, credit: bool = False) -> str:
    """Which document a cash/transfer sale produces.

    - not VAT registered: receipt (RC)
    - VAT registered + buyer details: full tax invoice (TAX)
    - VAT registered, no buyer: abbreviated invoice (ABB) only when the owner
      enabled it; otherwise buyer details are required (owner decision).
    - credit sale: delivery note / invoice (DN), always with a customer
    """
    if credit:
        return DOC_DN
    if settings.vat_mode != VAT_MODE_VAT:
        return DOC_RC
    if has_buyer:
        return DOC_TAX
    if settings.allow_abbreviated_invoice:
        return DOC_ABB
    raise DocumentError("ร้านจด VAT: กรุณากรอกชื่อและที่อยู่ผู้ซื้อเพื่อออกใบกำกับภาษี")


def prefix_for(settings: ShopSetting, doc_type: str) -> str:
    custom = (settings.doc_prefixes or {}).get(doc_type)
    return custom or DEFAULT_PREFIXES[doc_type]


def clean_prefixes(raw: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Validate prefixes typed by the owner. Returns (prefixes, errors by doc type).
    Prefixes must be unique so numbers of different documents can never collide."""
    prefixes, errors = {}, {}
    for doc_type, default in DEFAULT_PREFIXES.items():
        value = (raw.get(doc_type) or "").strip().upper() or default
        if not _PREFIX_RE.match(value):
            errors[doc_type] = "ตัวอักษรอังกฤษหรือตัวเลข 1–6 ตัว"
        prefixes[doc_type] = value
    seen: dict[str, str] = {}
    for doc_type, value in prefixes.items():
        if value in seen and doc_type not in errors:
            errors[doc_type] = f"ซ้ำกับ {TITLES[seen[value]]}"
        seen.setdefault(value, doc_type)
    return prefixes, errors


def period_for(when: datetime, reset: str) -> str:
    year = when.year + 543
    return f"{year}" if reset == "yearly" else f"{year}-{when.month:02d}"


def next_doc_no(
    db: Session, doc_type: str, when: datetime, reset: str = "monthly", prefix: str | None = None
) -> str:
    """Allocate the next number, e.g. RC2569-10-0001. Must run inside the
    caller's (IMMEDIATE) transaction; nothing is committed here."""
    period = period_for(when, reset)
    seq = db.scalar(
        select(DocSequence).where(DocSequence.doc_type == doc_type, DocSequence.period == period)
    )
    if seq is None:
        seq = DocSequence(doc_type=doc_type, period=period, last_no=0)
        db.add(seq)
    seq.last_no += 1
    db.flush()
    return f"{prefix or DEFAULT_PREFIXES[doc_type]}{period}-{seq.last_no:04d}"
