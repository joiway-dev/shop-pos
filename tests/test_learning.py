from sqlalchemy import select

from app.models import AuditLog, ProductAlias
from app.services.matching import learning, matcher
from tests.factories import seed_catalog


def test_learn_new_alias_then_auto_match(db, owner, cashier):
    p = seed_catalog(db, owner.id)
    assert matcher.match(db, "ปูนตราเสือถุงใหญ่").status != matcher.STATUS_AUTO
    r = learning.learn_alias(db, cashier.id, "ปูนตราเสือถุงใหญ่", p["tiger50"])
    db.commit()
    assert r.status == learning.LEARN_SAVED
    alias = db.scalars(select(ProductAlias).where(ProductAlias.alias_text == "ปูนตราเสือถุงใหญ่")).one()
    assert alias.source == "learned"
    assert matcher.match(db, "ปูนตราเสือ ถุงใหญ่").best.product_id == p["tiger50"].id
    assert matcher.match(db, "ปูนตราเสือ ถุงใหญ่").status == matcher.STATUS_AUTO
    log = db.scalars(select(AuditLog).where(AuditLog.action == "alias_learn")).one()
    assert log.user_id == cashier.id


def test_learn_conflict_is_not_saved(db, owner, cashier):
    p = seed_catalog(db, owner.id)
    r = learning.learn_alias(db, cashier.id, "ปูนเสือ", p["tiger40"])  # belongs to tiger50
    assert r.status == learning.LEARN_CONFLICT
    assert p["tiger50"].name in r.message
    assert db.scalar(
        select(ProductAlias).where(ProductAlias.product_id == p["tiger40"].id)
    ) is None


def test_learn_existing_alias_counts_hit(db, owner, cashier):
    p = seed_catalog(db, owner.id)
    r = learning.learn_alias(db, cashier.id, "ปูน เสือ", p["tiger50"])
    db.commit()
    assert r.status == learning.LEARN_ALREADY
    alias = db.scalars(select(ProductAlias).where(ProductAlias.alias_normalized == "ปูนเสือ")).one()
    db.refresh(alias)
    assert alias.hit_count == 1


def test_learn_skips_name_and_trivial_text(db, owner, cashier):
    p = seed_catalog(db, owner.id)
    assert learning.learn_alias(db, cashier.id, "ทรายหยาบ", p["sand"]).status == learning.LEARN_SKIPPED
    assert learning.learn_alias(db, cashier.id, "ท", p["sand"]).status == learning.LEARN_SKIPPED
    assert learning.learn_alias(db, cashier.id, " -- ", p["sand"]).status == learning.LEARN_SKIPPED
