from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.models import AuditLog, Base, ShopSetting
from app.services.audit import log_action


def test_sqlite_pragmas(app):
    with app.state.engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_migrations_match_models(app):
    with app.state.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_initial_shop_settings_row(db):
    row = db.get(ShopSetting, 1)
    assert row is not None
    assert row.vat_mode == "none"
    assert row.vat_rate_bp == 700
    assert row.price_includes_vat is True
    assert row.allow_abbreviated_invoice is False
    assert row.backup_keep_days == 30


def test_rollback_discards_changes(db):
    db.get(ShopSetting, 1).shop_name = "ชั่วคราว"
    db.flush()
    db.rollback()
    assert db.get(ShopSetting, 1).shop_name == ""


def test_audit_detail_keeps_thai_text(app, db, owner):
    log_action(db, owner.id, "test", "x", detail={"note": "ปูนเสือ"})
    db.commit()
    with app.state.engine.connect() as conn:
        raw = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'test'")
        ).scalar()
    assert "ปูนเสือ" in raw
    assert db.query(AuditLog).filter_by(action="test").one().detail == {"note": "ปูนเสือ"}
