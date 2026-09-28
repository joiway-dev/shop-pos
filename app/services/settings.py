"""Shop settings (single row in `shop_settings`)."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import ShopSetting
from app.models.settings import HEAD_OFFICE_BRANCH_NO, VAT_MODE_VAT, VAT_MODES
from app.services.audit import log_action

MAX_LOGO_BYTES = 1024 * 1024
_LOGO_SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}


class SettingsError(ValueError):
    """Validation failure. `errors` maps field name -> Thai message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass
class ShopSettingsInput:
    """Raw form values (strings) as typed by the owner."""

    shop_name: str = ""
    address: str = ""
    phone: str = ""
    vat_mode: str = "none"
    tax_id: str = ""
    branch_type: str = "head"  # "head" | "branch"
    branch_no: str = ""
    vat_rate: str = "7.00"
    price_includes_vat: bool = True
    allow_abbreviated_invoice: bool = False
    backup_dir: str = ""
    backup_keep_days: str = "30"
    match_auto_accept: str = "92"
    match_suggest_min: str = "60"
    match_min_gap: str = "8"
    doc_number_reset: str = "monthly"


def get_shop_settings(db: Session) -> ShopSetting:
    row = db.get(ShopSetting, 1)
    if row is None:
        # The initial migration inserts this row; recreate defensively.
        row = ShopSetting(id=1)
        db.add(row)
        db.flush()
    return row


def is_valid_thai_tax_id(tax_id: str) -> bool:
    """13-digit Thai tax ID with its mod-11 check digit."""
    if not re.fullmatch(r"[0-9]{13}", tax_id):
        return False
    total = sum(int(d) * (13 - i) for i, d in enumerate(tax_id[:12]))
    return (11 - total % 11) % 10 == int(tax_id[12])


def parse_vat_rate_bp(text: str) -> int:
    """'7' / '7.00' -> 700 basis points. Raises ValueError."""
    try:
        rate = Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError("อัตราภาษีต้องเป็นตัวเลข") from None
    if not rate.is_finite():
        raise ValueError("อัตราภาษีต้องเป็นตัวเลข")
    if rate != rate.quantize(Decimal("0.01")):
        raise ValueError("อัตราภาษีมีทศนิยมได้ไม่เกิน 2 ตำแหน่ง")
    if not Decimal("0") < rate < Decimal("100"):
        raise ValueError("อัตราภาษีต้องมากกว่า 0 และน้อยกว่า 100")
    return int(rate * 100)


def format_vat_rate(bp: int) -> str:
    return f"{Decimal(bp) / 100:.2f}"


def _digits(text: str) -> str:
    return re.sub(r"[\s-]", "", text or "")


def _validate(data: ShopSettingsInput) -> tuple[dict, dict[str, str]]:
    errors: dict[str, str] = {}
    values: dict = {
        "shop_name": " ".join(data.shop_name.split()),
        "address": data.address.strip(),
        "phone": data.phone.strip(),
        "price_includes_vat": bool(data.price_includes_vat),
        "allow_abbreviated_invoice": bool(data.allow_abbreviated_invoice),
    }
    if not values["shop_name"]:
        errors["shop_name"] = "กรุณากรอกชื่อร้าน"

    if data.vat_mode not in VAT_MODES:
        errors["vat_mode"] = "รูปแบบภาษีไม่ถูกต้อง"
    values["vat_mode"] = data.vat_mode
    is_vat = data.vat_mode == VAT_MODE_VAT

    tax_id = _digits(data.tax_id)
    if tax_id and not is_valid_thai_tax_id(tax_id):
        errors["tax_id"] = "เลขประจำตัวผู้เสียภาษีไม่ถูกต้อง (ต้องเป็นตัวเลข 13 หลักที่ถูกต้อง)"
    elif is_vat and not tax_id:
        errors["tax_id"] = "โหมดจด VAT ต้องกรอกเลขประจำตัวผู้เสียภาษี 13 หลัก"
    values["tax_id"] = tax_id or None

    if data.branch_type == "head":
        values["branch_no"] = HEAD_OFFICE_BRANCH_NO
    elif data.branch_type == "branch":
        branch_no = _digits(data.branch_no)
        if re.fullmatch(r"[0-9]{1,5}", branch_no) and int(branch_no) > 0:
            values["branch_no"] = branch_no.zfill(5)
        else:
            errors["branch_no"] = "เลขสาขาต้องเป็นตัวเลข 1–5 หลัก (ไม่ใช่ 00000)"
    else:
        errors["branch_no"] = "กรุณาเลือกสำนักงานใหญ่หรือสาขา"

    try:
        values["vat_rate_bp"] = parse_vat_rate_bp(data.vat_rate)
    except ValueError as e:
        errors["vat_rate"] = str(e)

    backup_dir = data.backup_dir.strip()
    if backup_dir and not Path(backup_dir).is_absolute():
        errors["backup_dir"] = "ที่เก็บไฟล์สำรองต้องเป็น path เต็ม เช่น D:\\backup-shop"
    values["backup_dir"] = backup_dir or None

    try:
        keep_days = int(data.backup_keep_days.strip())
        if not 1 <= keep_days <= 3650:
            raise ValueError
        values["backup_keep_days"] = keep_days
    except ValueError:
        errors["backup_keep_days"] = "จำนวนวันเก็บไฟล์สำรองต้องเป็น 1–3650"

    if data.doc_number_reset not in ("monthly", "yearly"):
        errors["doc_number_reset"] = "รูปแบบเลขที่เอกสารไม่ถูกต้อง"
    values["doc_number_reset"] = data.doc_number_reset

    for field, low, high, label in (
        ("match_auto_accept", 1, 100, "คะแนนลงบิลอัตโนมัติ"),
        ("match_suggest_min", 1, 100, "คะแนนขั้นต่ำที่จะแนะนำ"),
        ("match_min_gap", 0, 50, "ระยะห่างจากอันดับ 2"),
    ):
        try:
            number = int(getattr(data, field).strip())
            if not low <= number <= high:
                raise ValueError
            values[field] = number
        except ValueError:
            errors[field] = f"{label}ต้องเป็นตัวเลข {low}–{high}"
    if (
        "match_auto_accept" in values
        and "match_suggest_min" in values
        and values["match_suggest_min"] >= values["match_auto_accept"]
    ):
        errors["match_suggest_min"] = "คะแนนขั้นต่ำที่จะแนะนำต้องน้อยกว่าคะแนนลงบิลอัตโนมัติ"

    return values, errors


def update_shop_settings(db: Session, actor_id: int, data: ShopSettingsInput) -> ShopSetting:
    values, errors = _validate(data)
    if errors:
        raise SettingsError(errors)
    row = get_shop_settings(db)
    changes = {}
    for field, new in values.items():
        old = getattr(row, field)
        if old != new:
            changes[field] = [old, new]
            setattr(row, field, new)
    if changes:
        log_action(db, actor_id, "settings_update", "shop_settings", row.id, {"changes": changes})
    return row


def save_logo(
    db: Session, actor_id: int, uploads_dir: Path, content: bytes
) -> ShopSetting:
    if len(content) > MAX_LOGO_BYTES:
        raise SettingsError({"logo": "ไฟล์โลโก้ต้องไม่เกิน 1 MB"})
    ext = next((e for sig, e in _LOGO_SIGNATURES.items() if content.startswith(sig)), None)
    if ext is None:
        raise SettingsError({"logo": "โลโก้ต้องเป็นไฟล์ PNG หรือ JPG"})
    uploads_dir.mkdir(parents=True, exist_ok=True)
    filename = f"logo.{ext}"
    for old in uploads_dir.glob("logo.*"):
        old.unlink()
    (uploads_dir / filename).write_bytes(content)

    row = get_shop_settings(db)
    row.logo_filename = filename
    log_action(db, actor_id, "logo_update", "shop_settings", row.id, {"file": filename})
    return row
