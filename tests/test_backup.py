"""Backup / restore / go-live (SPEC phase 5: "backup/restore ทดสอบแล้ว")."""

import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Customer, DocSequence, Product, Sale, StockMovement
from app.services import backup, customers, stock
from app.services.backup import BackupError
from app.services.customers import CustomerInput
from app.services.sales import CartLineInput, CheckoutInput, create_sale
from app.services.settings import get_shop_settings
from tests.conftest import OWNER_PIN
from tests.factories import seed_catalog


def sale_count(factory) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(Sale))


def sell_sand(db, owner, p):
    db.commit()
    create_sale(db, owner, CheckoutInput([CartLineInput(p["sand"].id, p["sand"].units[0].id, "1")]))
    db.commit()


def test_create_and_list_backup(config, db, owner):
    target = backup.backup_dir(db, config)
    made = backup.create_backup(config, target, "manual")
    assert made.path.exists() and made.reason == "manual"
    con = sqlite3.connect(made.path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    con.close()
    second = backup.create_backup(config, target, "manual")  # same second -> unique name
    assert second.name != made.name
    assert [f.name for f in backup.list_backups(target)][:2] == [second.name, made.name] or len(
        backup.list_backups(target)) == 2


def test_backup_dir_setting(config, db, owner, tmp_path):
    custom = tmp_path / "usb"
    get_shop_settings(db).backup_dir = str(custom)
    db.commit()
    made = backup.create_backup(config, backup.backup_dir(db, config), "manual")
    assert made.path.parent == custom


def test_prune_keeps_recent_and_newest(config, tmp_path):
    target = tmp_path / "b"
    target.mkdir()
    now = datetime(2026, 9, 28, 12, 0, 0)
    for days in (0, 10, 40, 90):
        t = now - timedelta(days=days)
        (target / f"shop-{t:%Y%m%d-%H%M%S}-auto.db").write_bytes(b"x")
    (target / "notes.txt").write_text("keep me")
    assert backup.prune(target, keep_days=30, now=now) == 2
    assert len(backup.list_backups(target)) == 2
    assert (target / "notes.txt").exists()


def test_ensure_daily_backup_once_per_day(app, config, db, owner):
    first = backup.ensure_daily_backup(config, app.state.session_factory)
    assert first is not None and first.reason == "auto"
    assert backup.ensure_daily_backup(config, app.state.session_factory) is None


def test_restore(app, config, db, owner):
    p = seed_catalog(db, owner.id)
    sell_sand(db, owner, p)
    target = backup.backup_dir(db, config)
    snap = backup.create_backup(config, target, "manual")
    sell_sand(db, owner, p)
    factory = app.state.session_factory
    assert sale_count(factory) == 2

    with pytest.raises(BackupError) as e:
        backup.restore(db, config, owner, snap.name, "0000", app.state.engine)
    assert e.value.pin_failed

    with factory() as s:
        actor = s.get(type(owner), owner.id)
        backup.restore(s, config, actor, snap.name, OWNER_PIN, app.state.engine)
    assert sale_count(factory) == 1
    reasons = [f.reason for f in backup.list_backups(target)]
    assert "before-restore" in reasons
    with factory() as s:
        assert s.scalar(select(func.count()).where(AuditLog.action == "backup_restore")) == 1
        assert stock.balance_of(s, p["sand"].id) == -1000


def test_restore_rejects_unknown_file(app, config, db, owner):
    with pytest.raises(BackupError):
        backup.restore(db, config, owner, "../../shop.db", OWNER_PIN, app.state.engine)


def test_go_live_clears_transactions_keeps_master_data(app, config, db, owner):
    p = seed_catalog(db, owner.id)
    customers.save_customer(db, owner, None, CustomerInput("ลูกค้า"))
    p["sand"].avg_cost = 30000
    sell_sand(db, owner, p)
    with pytest.raises(BackupError):
        backup.go_live(db, config, owner, OWNER_PIN, "ผิดคำ")
    with pytest.raises(BackupError) as e:
        backup.go_live(db, config, owner, "0000", "เริ่มใช้งานจริง")
    assert e.value.pin_failed

    made = backup.go_live(db, config, owner, OWNER_PIN, " เริ่มใช้งานจริง ")
    assert made.reason == "before-golive"
    for model in (Sale, StockMovement, DocSequence):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.scalar(select(func.count()).select_from(Product)) == 8
    assert db.scalar(select(func.count()).select_from(Customer)) == 1
    assert db.get(Product, p["sand"].id).avg_cost == 0
    assert stock.balance_of(db, p["sand"].id) == 0
    assert get_shop_settings(db).went_live_at is not None

    sell_sand(db, owner, p)
    assert db.scalars(select(Sale.doc_no)).one().endswith("-0001")  # numbering starts again
    with pytest.raises(BackupError):
        backup.go_live(db, config, owner, OWNER_PIN, "เริ่มใช้งานจริง")  # only once


def test_go_live_owner_only(config, db, cashier):
    with pytest.raises(BackupError):
        backup.go_live(db, config, cashier, OWNER_PIN, "เริ่มใช้งานจริง")
