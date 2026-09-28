import pytest

from app.services.costing import REF_OPENING, REF_PURCHASE_LINE, REF_PURCHASE_VOID, moving_average, replay
from app.services.purchases import bill_totals, cost_per_base


# --- moving average formula (SPEC phase 3) --------------------------------------


@pytest.mark.parametrize("qty_before,avg_before,qty_in,cost_in,expected", [
    (10_000, 10000, 10_000, 12000, 11000),   # 10 @100 + 10 @120 -> 110
    (30_000, 14000, 10_000, 15000, 14250),   # 30 @140 + 10 @150 -> 142.50
    (0, 14000, 10_000, 15000, 15000),        # no stock -> new cost
    (-5_000, 14000, 10_000, 15000, 15000),   # negative stock -> new cost (SPEC)
    (1_000, 10000, 2_000, 10001, 10001),     # (100 + 200.02)/3 = 100.0067 -> 100.01
    (2_500, 1235, 500, 1300, 1246),          # fractional quantities
])
def test_moving_average(qty_before, avg_before, qty_in, cost_in, expected):
    assert moving_average(qty_before, avg_before, qty_in, cost_in) == expected


# --- replay of movement history -------------------------------------------------


def purchase(qty, line_id):
    return (qty, 0, REF_PURCHASE_LINE, line_id)


def sale(qty):
    return (-qty, 0, None, None)


def test_replay_purchases_and_sales():
    moves = [purchase(10_000, 1), sale(4_000), purchase(10_000, 2)]
    # after sale: 6 @100; + 10 @130 -> (600 + 1300)/16 = 118.75
    assert replay(moves, {1: 10000, 2: 13000}, set()) == 11875


def test_replay_sold_out_then_buy_uses_new_cost():
    moves = [purchase(5_000, 1), sale(8_000), purchase(10_000, 2)]
    assert replay(moves, {1: 10000, 2: 13000}, set()) == 13000


def test_replay_pending_cost_does_not_change_avg_until_entered():
    moves = [purchase(10_000, 1), purchase(10_000, 2)]
    assert replay(moves, {1: 10000, 2: None}, set()) == 10000
    # Cost entered later: history is replayed in the original order.
    assert replay(moves, {1: 10000, 2: 12000}, set()) == 11000


def test_replay_skips_voided_purchase():
    moves = [purchase(10_000, 1), purchase(10_000, 2), (-10_000, 0, REF_PURCHASE_VOID, 2)]
    assert replay(moves, {1: 10000, 2: 50000}, {2}) == 10000


def test_replay_opening_sets_cost():
    moves = [purchase(10_000, 1), (5_000, 9000, REF_OPENING, None), purchase(15_000, 2)]
    # opening sets 90.00 with 15 in stock; + 15 @110 -> 100
    assert replay(moves, {1: 10000, 2: 11000}, set()) == 10000


def test_replay_sale_void_does_not_change_cost():
    moves = [purchase(10_000, 1), sale(5_000), (5_000, 10000, None, None), purchase(10_000, 2)]
    assert replay(moves, {1: 10000, 2: 13000}, set()) == 11500


# --- supplier bill arithmetic ---------------------------------------------------


@pytest.mark.parametrize("unit_cost,factor,vat_type,shop_vat,expected", [
    (560000, 40_000, "none", False, 14000),     # 5,600 per pallet of 40 bags -> 140 per bag
    (10700, 1_000, "included", True, 10000),    # VAT shop: cost excludes VAT
    (10700, 1_000, "included", False, 10700),   # non-VAT shop: VAT is part of the cost
    (10000, 1_000, "excluded", True, 10000),
    (10000, 1_000, "excluded", False, 10700),
    (100000, 3_000, "none", False, 33333),      # 1,000 / 3 -> 333.33
])
def test_cost_per_base(unit_cost, factor, vat_type, shop_vat, expected):
    assert cost_per_base(unit_cost, factor, vat_type, 700, shop_vat) == expected


def test_bill_totals():
    assert bill_totals([10700, 5350], "included", 700) == (16050, 1050, 16050)
    assert bill_totals([10000, 5000], "excluded", 700) == (15000, 1050, 16050)
    assert bill_totals([10000], "none", 0) == (10000, 0, 10000)
