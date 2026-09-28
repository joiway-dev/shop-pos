import pytest

from app.services.matching import order_parser as op
from tests.factories import seed_catalog


@pytest.fixture
def products(db, owner):
    return seed_catalog(db, owner.id)


def parse_one(db, text):
    lines = op.parse_order(db, text)
    assert len(lines) == 1
    return lines[0]


def unit_id(product, name):
    return next(u.id for u in product.units if u.unit_name == name)


@pytest.mark.parametrize(
    "text",
    ["ปูนเสือ 10 ถุง", "ปูนเสือ 10ถุง", "ปูนเสือ x10", "10 ถุง ปูนเสือ", "ปูนเสือ ๑๐ ถุง", "- ปูนเสือ 10 ถุงค่ะ"],
)
def test_spec_quantity_formats(db, products, text):
    line = parse_one(db, text)
    assert line.status == op.STATUS_OK
    assert line.product_id == products["tiger50"].id
    assert line.qty == 10_000
    assert line.unit_id == unit_id(products["tiger50"], "ถุง")


def test_size_number_is_not_quantity(db, products):
    line = parse_one(db, "เหล็ก 12 มิล 20 เส้น")
    assert line.status == op.STATUS_OK
    assert line.product_id == products["rebar12"].id
    assert line.qty == 20_000
    assert line.unit_name == "เส้น"
    assert line.product_text == "เหล็ก 12 มิล"


def test_size_only_gives_qty_one_for_review(db, products):
    line = parse_one(db, "เหล็ก 12 มิล")
    assert line.product_id == products["rebar12"].id
    assert line.qty == 1000
    assert line.review_note


def test_other_sale_unit(db, products):
    line = parse_one(db, "ปูนเสือ 2 พาเลท")
    assert (line.status, line.qty, line.unit_id) == (op.STATUS_OK, 2000, unit_id(products["tiger50"], "พาเลท"))


def test_unit_spelling_variants(db, products):
    line = parse_one(db, "สายไฟ VAF 2x1.5 25 ม.")
    assert line.status == op.STATUS_OK
    assert line.product_id == products["wire"].id
    assert (line.qty, line.unit_name) == (25_000, "เมตร")


def test_decimal_quantity(db, products):
    line = parse_one(db, "ทรายหยาบ 0.5 คิว")
    assert (line.status, line.qty, line.unit_name) == (op.STATUS_OK, 500, "คิว")


def test_unit_not_sold_for_product_needs_choice(db, products):
    line = parse_one(db, "ทรายหยาบ 3 ถุง")
    assert line.status == op.STATUS_NEEDS_CHOICE
    assert line.product_id == products["sand"].id
    assert line.unit_id is None
    assert [u.unit_name for u in line.unit_choices] == ["คิว"]


def test_no_quantity_defaults_to_one_default_unit(db, products):
    line = parse_one(db, "ตะปู 2 นิ้ว")
    assert line.status == op.STATUS_OK
    assert (line.qty, line.unit_name) == (1000, "กล่อง")  # default sale unit
    assert "ไม่ได้ระบุจำนวน" in line.review_note


def test_bare_trailing_number_is_qty_in_default_unit(db, products):
    line = parse_one(db, "ทรายหยาบ 3")
    assert (line.status, line.qty, line.unit_name) == (op.STATUS_OK, 3000, "คิว")
    assert "ไม่ได้ระบุหน่วย" in line.review_note


def test_greeting_line_is_skipped_visibly(db, products):
    lines = op.parse_order(db, "สวัสดีค่ะ\nปูนเสือ 5 ถุง\n\nขอบคุณค่ะ")
    assert [l.status for l in lines] == [op.STATUS_SKIPPED, op.STATUS_OK, op.STATUS_SKIPPED]
    assert [l.line_no for l in lines] == [1, 2, 4]  # blank line dropped


def test_unknown_product_with_number_is_not_found(db, products):
    line = parse_one(db, "กระเบื้องหลังคา 20 แผ่น")
    assert line.status == op.STATUS_NOT_FOUND
    assert "ไม่พบ" in line.message


def test_ambiguous_product_needs_choice(db, products):
    line = parse_one(db, "ปูนซีเมนต์ตราเสือ 5 ถุง")
    assert line.status == op.STATUS_NEEDS_CHOICE
    assert len(line.candidates) >= 2
    assert line.qty == 5000 and line.unit_text == "ถุง"


def test_choose_resolves_line(db, products):
    line = parse_one(db, "ปูนซีเมนต์ตราเสือ 5 ถุง")
    op.choose(db, line, products["tiger40"].id)
    assert (line.status, line.product_id, line.unit_name) == (op.STATUS_OK, products["tiger40"].id, "ถุง")


def test_choose_unit(db, products):
    line = parse_one(db, "ทรายหยาบ 3 ถุง")
    op.choose(db, line, products["sand"].id, unit_id(products["sand"], "คิว"))
    assert (line.status, line.unit_name) == (op.STATUS_OK, "คิว")


def test_barcode_line(db, products):
    line = parse_one(db, "PAL-TIGER50")
    assert line.status == op.STATUS_OK
    assert line.unit_name == "พาเลท"


def test_numbered_list_from_line(db, products):
    lines = op.parse_order(db, "1. ปูนเสือ 10 ถุง\n2) เหล็ก 9 มิล 30 เส้น\n3. ทราย 2 คิว")
    assert [l.status for l in lines] == [op.STATUS_OK] * 3
    assert [l.qty for l in lines] == [10_000, 30_000, 2_000]
    assert lines[1].product_id == products["rebar9"].id


def test_polite_words_are_ignored(db, products):
    line = parse_one(db, "ขอ ทรายหยาบ 2 คิว ด้วยนะคะ")
    assert (line.status, line.product_id, line.qty) == (op.STATUS_OK, products["sand"].id, 2000)


def test_ready_to_bill(db, products):
    ok = op.parse_order(db, "สวัสดีครับ\nปูนเสือ 5 ถุง")
    assert op.ready_to_bill(ok)
    blocked = op.parse_order(db, "ปูนเสือ 5 ถุง\nทรายหยาบ 3 ถุง")
    assert not op.ready_to_bill(blocked)
    assert not op.ready_to_bill(op.parse_order(db, "สวัสดีครับ"))
