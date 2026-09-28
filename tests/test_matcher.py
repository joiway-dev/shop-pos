import pytest

from app.services import catalog
from app.services.matching import matcher
from app.services.matching.learning import record_alias_hit
from app.services.matching.matcher import MatchConfig
from tests.factories import make_product, seed_catalog


@pytest.fixture
def products(db, owner):
    return seed_catalog(db, owner.id)


def test_product_barcode(db, products):
    r = matcher.match(db, "8851234567890")
    assert r.status == matcher.STATUS_AUTO
    assert len(r.candidates) == 1
    best = r.best
    assert (best.product_id, best.unit_id, best.score, best.reason) == (products["tiger50"].id, None, 100, "barcode")


def test_unit_barcode_returns_unit(db, products):
    best = matcher.match(db, " PAL-TIGER50 ").best
    pallet = next(u for u in products["tiger50"].units if u.unit_name == "พาเลท")
    assert (best.product_id, best.unit_id, best.score) == (products["tiger50"].id, pallet.id, 100)


def test_exact_alias_auto_accepts(db, products):
    r = matcher.match(db, "ปูน เสือ")
    assert r.status == matcher.STATUS_AUTO
    assert (r.best.product_id, r.best.score, r.best.reason) == (products["tiger50"].id, 100, "alias")


def test_alias_matches_across_spellings(db, products):
    r = matcher.match(db, "เหล็ก 12mm")
    assert r.status == matcher.STATUS_AUTO
    assert r.best.product_id == products["rebar12"].id


def test_exact_name_scores_98(db, products):
    r = matcher.match(db, "ทรายหยาบ")
    assert (r.best.product_id, r.best.score, r.best.reason) == (products["sand"].id, 98, "name")
    assert r.status == matcher.STATUS_AUTO


def test_contains_scores_85_to_95(db, products):
    candidates = matcher.rank(matcher.get_index(db), "ตราเสือ", MatchConfig())
    contains = [c for c in candidates if c.reason == "contains"]
    assert {c.product_id for c in contains} >= {products["tiger50"].id, products["tiger40"].id}
    assert all(85 <= c.score <= 95 for c in contains)


def test_ambiguous_query_needs_choice(db, products):
    r = matcher.match(db, "ปูนซีเมนต์ตราเสือ")
    assert r.status == matcher.STATUS_NEEDS_CHOICE
    ids = [c.product_id for c in r.candidates[:2]]
    assert set(ids) == {products["tiger50"].id, products["tiger40"].id}


def test_fuzzy_typo_is_capped_and_needs_choice(db, products):
    r = matcher.match(db, "ทรายหยาย")  # typo
    assert r.best.product_id == products["sand"].id
    assert r.best.score <= matcher.FUZZY_CAP
    assert r.status == matcher.STATUS_NEEDS_CHOICE


def test_not_found(db, products):
    r = matcher.match(db, "กระเบื้องหลังคา")
    assert r.status == matcher.STATUS_NOT_FOUND
    assert r.candidates == []


def test_empty_query(db, products):
    assert matcher.match(db, "   ").status == matcher.STATUS_NOT_FOUND


def test_results_sorted_and_limited(db, owner, products):
    for i in range(8):
        make_product(db, owner.id, f"สีน้ำ รุ่น {i}", "แกลลอน", "300")
    db.commit()
    r = matcher.match(db, "สีน้ำ")
    assert len(r.candidates) == matcher.MAX_CHOICES
    scores = [c.score for c in r.candidates]
    assert scores == sorted(scores, reverse=True)


def test_gap_rule_blocks_auto_accept(db, owner, products):
    # Two products share an alias -> both 100 -> must choose.
    catalog.add_alias(db, owner.id, products["tiger40"], "ปูนเสือ")
    db.commit()
    r = matcher.match(db, "ปูนเสือ")
    assert r.status == matcher.STATUS_NEEDS_CHOICE
    assert [c.score for c in r.candidates[:2]] == [100, 100]


def test_hit_count_breaks_ties(db, owner, products):
    catalog.add_alias(db, owner.id, products["tiger40"], "ปูนเสือ")
    db.commit()
    record_alias_hit(db, products["tiger40"].id, "ปูนเสือ")
    catalog.add_alias(db, owner.id, products["sand"], "ตัวกระตุ้นให้ล้างแคช")  # force index rebuild
    db.commit()
    assert matcher.match(db, "ปูนเสือ").best.product_id == products["tiger40"].id


def test_decide_thresholds():
    c = lambda score, pid=1: matcher.MatchCandidate(pid, "x", score, "fuzzy")  # noqa: E731
    cfg = MatchConfig(auto_accept=92, suggest_min=60, min_gap=8)
    assert matcher.decide("q", [c(92)], cfg).status == matcher.STATUS_AUTO
    assert matcher.decide("q", [c(91)], cfg).status == matcher.STATUS_NEEDS_CHOICE
    assert matcher.decide("q", [c(100), c(92, 2)], cfg).status == matcher.STATUS_AUTO
    assert matcher.decide("q", [c(100), c(93, 2)], cfg).status == matcher.STATUS_NEEDS_CHOICE
    assert matcher.decide("q", [], cfg).status == matcher.STATUS_NOT_FOUND


def test_thresholds_come_from_settings(db, products):
    settings = matcher.get_shop_settings(db)
    settings.match_auto_accept = 99
    db.commit()
    assert matcher.match(db, "ทรายหยาบ").status == matcher.STATUS_NEEDS_CHOICE  # 98 < 99


def test_inactive_products_are_not_matched(db, owner, products):
    catalog.set_product_active(db, owner.id, products["sand"], False)
    db.commit()
    assert matcher.match(db, "ทรายหยาบ").status == matcher.STATUS_NOT_FOUND


def test_index_refreshes_after_commit(db, owner, products):
    assert matcher.match(db, "อิฐมอญ").status == matcher.STATUS_NOT_FOUND
    make_product(db, owner.id, "อิฐมอญ", "ก้อน", "1.20")
    db.commit()
    assert matcher.match(db, "อิฐมอญ").status == matcher.STATUS_AUTO


def test_rolled_back_change_not_indexed(db, owner, products):
    make_product(db, owner.id, "อิฐบล็อก", "ก้อน", "8")
    db.rollback()
    assert matcher.match(db, "อิฐบล็อก").status == matcher.STATUS_NOT_FOUND
