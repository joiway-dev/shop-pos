import pytest

from app.services.money import (
    AmountError,
    format_money,
    format_qty,
    parse_money,
    parse_qty,
)


@pytest.mark.parametrize(
    "text,satang",
    [("145", 14500), ("145.5", 14550), ("1,250.50", 125050), ("0", 0), ("๑๒", 1200), (" 0.01 ", 1)],
)
def test_parse_money(text, satang):
    value = parse_money(text)
    assert value == satang and isinstance(value, int)


@pytest.mark.parametrize("text", ["", "abc", "-1", "1.005", "NaN", "Infinity"])
def test_parse_money_invalid(text):
    with pytest.raises(AmountError):
        parse_money(text)


@pytest.mark.parametrize("text,milli", [("1", 1000), ("2.5", 2500), ("0.125", 125), ("40", 40000)])
def test_parse_qty(text, milli):
    assert parse_qty(text) == milli


@pytest.mark.parametrize("text", ["0", "-2", "0.0001", "x"])
def test_parse_qty_invalid(text):
    with pytest.raises(AmountError):
        parse_qty(text)


def test_parse_qty_allow_zero():
    assert parse_qty("0", allow_zero=True) == 0


@pytest.mark.parametrize("satang,text", [(125050, "1,250.50"), (5, "0.05"), (-150, "-1.50"), (0, "0.00")])
def test_format_money(satang, text):
    assert format_money(satang) == text


@pytest.mark.parametrize("milli,text", [(2500, "2.5"), (40000, "40"), (1234567, "1,234.567"), (125, "0.125")])
def test_format_qty(milli, text):
    assert format_qty(milli) == text
