"""Backups, restore and the one-time "go live" reset (SPEC phase 5).

Backups use SQLite's online backup API (never a plain file copy of an open
database). Files are named shop-YYYYMMDD-HHMMSS-<reason>.db.
"""

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.config import BASE_DIR, AppConfig
from app.models import (
    ArAllocation, ArPayment, DailyClose, DocSequence, Product, Purchase, PurchaseLine, Quotation, QuotationLine, Sale,
    SaleLine, StockBalance, StockMovement, User,
)
from app.models.base import utcnow
from app.services import auth
from app.services.audit import log_action
from app.services.matching import matcher
from app.services.settings import get_shop_settings

_NAME_RE = re.compile(r"^shop-(\d{8})-(\d{6})-([a-z0-9-]+)\.db$")
_lock = threading.Lock()
GO_LIVE_CONFIRM = "เริ่มใช้งานจริง"


class BackupError(ValueError):
    def __init__(self, message: str, pin_failed: bool = False):
        super().__init__(message)
        self.pin_failed = pin_failed


@dataclass
class BackupFile:
    path: Path
    created: datetime
    reason: str
    size: int

    @property
    def name(self) -> str:
        return self.path.name


REASON_LABELS = {"auto": "อัตโนมัติรายวัน", "manual": "กดสำรองเอง", "close": "ตอนปิดยอด",
                 "before-restore": "ก่อนกู้คืน", "before-golive": "ก่อนเริ่มใช้งานจริง"}


def backup_dir(db: Session, config: AppConfig) -> Path:
    custom = get_shop_settings(db).backup_dir
    return Path(custom) if custom else config.default_backup_dir


def create_backup(config: AppConfig, target_dir: Path, reason: str = "manual") -> BackupFile:
    """Consistent snapshot of the live database into target_dir."""
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError(f"สร้างโฟลเดอร์สำรองไม่ได้: {target_dir} ({e.strerror})") from None
    with _lock:
        now = datetime.now()
        final = target_dir / f"shop-{now:%Y%m%d-%H%M%S}-{reason}.db"
        n = 1
        while final.exists():
            final = target_dir / f"shop-{now:%Y%m%d-%H%M%S}-{reason}-{n}.db"
            n += 1
        tmp = final.with_suffix(".tmp")
        src = sqlite3.connect(config.db_path)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            tmp.unlink(missing_ok=True)
            raise BackupError("ไฟล์สำรองเสีย (integrity check ไม่ผ่าน)")
        tmp.replace(final)
    return _describe(final)


def _describe(path: Path) -> BackupFile | None:
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    created = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    reason = re.sub(r"-\d+$", "", m.group(3))
    return BackupFile(path, created, reason, path.stat().st_size)


def list_backups(target_dir: Path) -> list[BackupFile]:
    if not target_dir.exists():
        return []
    files = [_describe(p) for p in target_dir.glob("shop-*.db")]
    return sorted((f for f in files if f), key=lambda f: f.created, reverse=True)


def prune(target_dir: Path, keep_days: int, now: datetime | None = None) -> int:
    """Delete backups older than keep_days (always keeps the newest one)."""
    now = now or datetime.now()
    files = list_backups(target_dir)
    removed = 0
    for f in files[1:]:
        if f.created < now - timedelta(days=keep_days):
            f.path.unlink(missing_ok=True)
            removed += 1
    return removed


def ensure_daily_backup(config: AppConfig, session_factory) -> BackupFile | None:
    """Make today's automatic backup if there is none yet, then prune."""
    with session_factory() as db:
        target = backup_dir(db, config)
        keep = get_shop_settings(db).backup_keep_days
    today = datetime.now().date()
    if any(f.created.date() == today for f in list_backups(target)):
        return None
    made = create_backup(config, target, "auto")
    prune(target, keep)
    return made


# --- restore ------------------------------------------------------------------------


def restore(db: Session, config: AppConfig, actor: User, filename: str, owner_pin: str, engine) -> BackupFile:
    """Replace the live data with a backup file (owner PIN). A backup of the
    current data is taken first. The caller must not use `db` afterwards."""
    approver = auth.verify_owner_pin(db, owner_pin or "")
    if approver is None:
        db.commit()  # keep the failed-PIN counter
        raise BackupError("PIN เจ้าของร้านไม่ถูกต้อง", pin_failed=True)
    target = backup_dir(db, config)
    chosen = next((f for f in list_backups(target) if f.name == filename), None)
    if chosen is None:
        raise BackupError("ไม่พบไฟล์สำรองที่เลือก")
    check = sqlite3.connect(chosen.path)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("ไฟล์สำรองนี้เสีย กู้คืนไม่ได้")
    finally:
        check.close()
    db.close()

    safety = create_backup(config, target, "before-restore")
    engine.dispose()
    with _lock:
        src = sqlite3.connect(chosen.path)
        dst = sqlite3.connect(config.db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    engine.dispose()
    # An older backup may predate the current program: bring its schema up to date.
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", config.db_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    matcher.invalidate_index()

    with Session(engine) as s:
        log_action(s, None, "backup_restore", "backup", None, {
            "file": chosen.name, "by": actor.name, "approved_by": approver.name, "safety_backup": safety.name,
        })
        s.commit()
    return chosen


# --- go live --------------------------------------------------------------------------


def go_live(db: Session, config: AppConfig, actor: User, owner_pin: str, confirm_text: str) -> BackupFile:
    """One-time reset before real use: back up, then clear every transaction
    (sales, purchases, stock, debts, quotations, numbering, daily closes).
    Products, prices, aliases, customers, suppliers, users and settings stay.
    This is the only place rows are deleted (owner decision, phase 5)."""
    if not actor.is_owner:
        raise BackupError("เฉพาะเจ้าของร้าน")
    settings = get_shop_settings(db)
    if settings.went_live_at is not None:
        raise BackupError("ร้านนี้เริ่มใช้งานจริงไปแล้ว ล้างข้อมูลซ้ำไม่ได้")
    if (confirm_text or "").strip() != GO_LIVE_CONFIRM:
        raise BackupError(f"พิมพ์คำว่า \"{GO_LIVE_CONFIRM}\" เพื่อยืนยัน")
    approver = auth.verify_owner_pin(db, owner_pin or "")
    if approver is None:
        db.commit()
        raise BackupError("PIN เจ้าของร้านไม่ถูกต้อง", pin_failed=True)
    db.commit()
    safety = create_backup(config, backup_dir(db, config), "before-golive")

    counts = {}
    for model in (ArAllocation, ArPayment, QuotationLine, Quotation, SaleLine, Sale, PurchaseLine, Purchase,
                  StockMovement, StockBalance, DocSequence, DailyClose):
        counts[model.__tablename__] = db.execute(delete(model)).rowcount
    db.execute(update(Product).values(avg_cost=0))
    settings = get_shop_settings(db)
    settings.went_live_at = utcnow()
    log_action(db, actor.id, "go_live", "shop_settings", 1, {"deleted": counts, "backup": safety.name})
    db.commit()
    matcher.invalidate_index()
    return safety
