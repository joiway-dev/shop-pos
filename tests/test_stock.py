from sqlalchemy import select

from app.models import StockBalance
from app.services import stock
from tests.factories import make_product


def test_movements_update_balance_and_allow_negative(db, owner):
    p = make_product(db, owner.id, "ปูน", "ถุง", "145")
    stock.record_movement(db, p.id, 10_000, "adjust", owner.id, note="ยอดยกมา")
    stock.record_movement(db, p.id, -12_000, "sale", owner.id)
    db.commit()
    assert stock.balance_of(db, p.id) == -2000


def test_rebuild_matches_movements(db, owner):
    a = make_product(db, owner.id, "ปูน", "ถุง", "145")
    b = make_product(db, owner.id, "ทราย", "คิว", "450")
    stock.record_movement(db, a.id, 5000, "adjust", owner.id)
    stock.record_movement(db, a.id, -1500, "sale", owner.id)
    stock.record_movement(db, b.id, 2500, "adjust", owner.id)
    db.commit()
    # Corrupt the cache, then rebuild it from movements.
    db.scalars(select(StockBalance).where(StockBalance.product_id == a.id)).one().qty_base = 999
    db.commit()
    assert stock.rebuild_balances(db) == 2
    db.commit()
    assert stock.balances(db) == {a.id: 3500, b.id: 2500}


def test_balance_of_product_without_movements(db, owner):
    p = make_product(db, owner.id, "ปูน", "ถุง", "145")
    assert stock.balance_of(db, p.id) == 0
