import pytest

from app.services.money import AmountError
from app.services.pricing import LineInput, base_qty, compute_bill, line_gross, parse_discount


def bill(lines, disc=0, mode="vat", rate=700, incl=True):
    return compute_bill(lines, disc, mode, rate, incl)


def test_line_gross_rounds_half_up():
    assert line_gross(10_000, 14500) == 145000
    assert line_gross(2500, 1235) == 3088  # 30.875 -> 30.88
    assert line_gross(1500, 333) == 500  # 4.995 -> 5.00


def test_base_qty():
    assert base_qty(2000, 40000) == 80000  # 2 pallets x 40 bags
    assert base_qty(2500, 1000) == 2500


def test_no_vat_mode():
    t = bill([LineInput(10_000, 14500), LineInput(2500, 1235)], disc=88, mode="none")
    assert (t.subtotal, t.discount, t.vat_amount, t.total) == (148088, 88, 0, 148000)
    assert (t.vatable_amount, t.exempt_amount) == (0, 0)


def test_price_includes_vat():
    t = bill([LineInput(1000, 10700)])
    assert (t.vatable_amount, t.vat_amount, t.total) == (10000, 700, 10700)


def test_price_excludes_vat():
    t = bill([LineInput(1000, 10000)], incl=False)
    assert (t.vatable_amount, t.vat_amount, t.total) == (10000, 700, 10700)


def test_vat_rounded_once_per_bill_including_vat():
    # Per line: round(100 x 7/107) = 7 satang each -> 21. Per bill: round(300 x 7/107) = 20.
    t = bill([LineInput(1000, 100)] * 3)
    assert t.vat_amount == 20
    assert t.vatable_amount + t.vat_amount == t.total == 300


def test_vat_rounded_once_per_bill_excluding_vat():
    # Per line: round(10 x 0.07) = 1 satang each -> 3. Per bill: round(30 x 0.07) = 2.
    t = bill([LineInput(1000, 10)] * 3, incl=False)
    assert (t.vat_amount, t.total) == (2, 32)


def test_exempt_lines_and_bill_discount_allocation():
    lines = [LineInput(1000, 10700), LineInput(1000, 5000, vat_exempt=True)]
    t = bill(lines, disc=1570)  # 10% of the bill
    assert t.subtotal == 15700
    assert t.exempt_amount == 4500
    assert (t.vatable_amount, t.vat_amount) == (9000, 630)
    assert t.total == 14130 == t.vatable_amount + t.vat_amount + t.exempt_amount


def test_all_exempt_bill_has_no_vat():
    t = bill([LineInput(2000, 4500, vat_exempt=True)])
    assert (t.vat_amount, t.exempt_amount, t.total) == (0, 9000, 9000)


def test_line_discount():
    t = bill([LineInput(2000, 5000, discount=1000)], mode="none")
    assert t.line_totals == [9000] and t.total == 9000


def test_discount_limits():
    with pytest.raises(AmountError):
        bill([LineInput(1000, 5000, discount=5001)])
    with pytest.raises(AmountError):
        bill([LineInput(1000, 5000)], disc=5001)


@pytest.mark.parametrize("text,base,expected", [
    ("", 10000, 0), ("0", 10000, 0), ("12.50", 10000, 1250), ("5%", 10000, 500),
    ("2.5%", 3333, 83), ("100%", 4000, 4000), (" 10 % ", 10000, 1000),
])
def test_parse_discount(text, base, expected):
    assert parse_discount(text, base) == expected


@pytest.mark.parametrize("text", ["abc", "101%", "-5%", "x%", "200"])
def test_parse_discount_invalid(text):
    with pytest.raises(AmountError):
        parse_discount(text, 10000)
