import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Sale, StockMovement
from app.services import sales as svc
from app.services import stock
from app.services.sales import BuyerInput, CartLineInput, CheckoutInput, SaleError
from app.services.settings import get_shop_settings
from tests.conftest import OWNER_PIN
from tests.factories import seed_catalog

BUYER = BuyerInput(name="บริษัท ก่อสร้างดี จำกัด", address="99 ถ.พหลโยธิน กทม.", tax_id="0105555000005", branch="")


@pytest.fixture
def products(db, owner):
    return seed_catalog(db, owner.id)


def unit(product, name):
    return next(u for u in product.units if u.unit_name == name)


def line(product, unit_name, qty, discount=""):
    return CartLineInput(product.id, unit(product, unit_name).id, qty, discount)


def checkout(db, actor, lines, **kw):
    db.commit()  # tests reuse one session; start the sale with a clean slate
    sale = svc.create_sale(db, actor, CheckoutInput(lines=lines, **kw))
    db.commit()
    return sale


def set_vat(db, allow_abb=False, incl=True):
    s = get_shop_settings(db)
    s.vat_mode, s.tax_id, s.branch_no = "vat", "0105555000005", "00000"
    s.allow_abbreviated_invoice, s.price_includes_vat = allow_abb, incl
    db.commit()


# --- quote ---------------------------------------------------------------------


def test_quote_uses_database_prices(db, products):
    q = svc.quote(db, [line(products["tiger50"], "ถุง", "10"), line(products["tiger50"], "พาเลท", "1")])
    assert q.ok
    assert [l.line_total for l in q.lines] == [145000, 560000]
    assert q.totals.total == 705000


def test_quote_reports_line_errors(db, products):
    q = svc.quote(db, [
        line(products["sand"], "คิว", "0"),
        CartLineInput(products["sand"].id, unit(products["tiger50"], "ถุง").id, "1"),
        CartLineInput(99999, 1, "1"),
    ])
    assert not q.ok and q.totals is None
    assert [bool(l.error) for l in q.lines] == [True, True, True]


def test_quote_warns_on_negative_stock(db, owner, products):
    stock.record_movement(db, products["tiger50"].id, 30_000, "adjust", owner.id)
    db.commit()
    q = svc.quote(db, [line(products["tiger50"], "ถุง", "20"), line(products["tiger50"], "ถุง", "15")])
    assert q.lines[0].stock_warning is None
    assert "สต็อกไม่พอ" in q.lines[1].stock_warning


# --- create sale ---------------------------------------------------------------


def test_cash_sale_no_vat(db, owner, products):
    sale = checkout(db, owner, [line(products["tiger50"], "ถุง", "10"), line(products["wire"], "เมตร", "2.5")],
                    cash_received="2000")
    assert sale.doc_type == "RC" and sale.doc_no.startswith("RC") and sale.doc_no.endswith("-0001")
    assert (sale.total, sale.vat_amount, sale.cash_received, sale.change_amount) == (148000, 0, 200000, 52000)
    assert sale.shop_snapshot["vat_mode"] == "none"
    assert [l.product_name_snapshot for l in sale.lines] == ["ปูนซีเมนต์ตราเสือ 50 กก.", "สายไฟ VAF 2x1.5"]
    assert stock.balance_of(db, products["tiger50"].id) == -10_000
    assert stock.balance_of(db, products["wire"].id) == -2500
    assert db.scalar(select(func.count()).where(AuditLog.action == "sale_create")) == 1


def test_sale_in_other_unit_deducts_base_units(db, owner, products):
    checkout(db, owner, [line(products["tiger50"], "พาเลท", "2")], payment_type="transfer")
    assert stock.balance_of(db, products["tiger50"].id) == -80_000


def test_numbers_increment(db, owner, products):
    a = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    b = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert int(b.doc_no[-4:]) == int(a.doc_no[-4:]) + 1


def test_cash_defaults_to_exact_amount(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert (sale.cash_received, sale.change_amount) == (45000, 0)


@pytest.mark.parametrize("kw,message", [
    ({"cash_received": "100"}, "รับเงินไม่พอ"),
    ({"payment_type": "credit"}, "เฟส 4"),
    ({"payment_type": "cheque"}, "วิธีชำระ"),
    ({"bill_discount": "99999"}, "ส่วนลด"),
])
def test_sale_errors(db, owner, products, kw, message):
    with pytest.raises(SaleError) as e:
        checkout(db, owner, [line(products["sand"], "คิว", "1")], **kw)
    assert message in str(e.value)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_empty_cart(db, owner):
    with pytest.raises(SaleError):
        checkout(db, owner, [])


def test_vat_requires_buyer_when_abb_disabled(db, owner, products):
    set_vat(db)
    with pytest.raises(SaleError) as e:
        checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert "ผู้ซื้อ" in str(e.value)


def test_full_tax_invoice(db, owner, products):
    set_vat(db)
    sale = checkout(db, owner, [line(products["tiger50"], "ถุง", "10")], buyer=BUYER, payment_type="transfer")
    assert sale.doc_type == "TAX" and sale.doc_no.startswith("INV")
    assert (sale.total, sale.vatable_amount, sale.vat_amount) == (145000, 135514, 9486)
    assert sale.vat_rate_snapshot == 700
    assert sale.buyer_snapshot == {"name": BUYER.name, "address": BUYER.address,
                                   "tax_id": "0105555000005", "branch_no": "00000"}


def test_abbreviated_invoice_when_enabled(db, owner, products):
    set_vat(db, allow_abb=True)
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert sale.doc_type == "ABB" and sale.buyer_snapshot is None


def test_vat_excluded_prices(db, owner, products):
    set_vat(db, allow_abb=True, incl=False)
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert (sale.vatable_amount, sale.vat_amount, sale.total) == (45000, 3150, 48150)


@pytest.mark.parametrize("buyer,message", [
    (BuyerInput(name="ก", address=""), "ที่อยู่"),
    (BuyerInput(name="ก", address="x", tax_id="123"), "เลขประจำตัว"),
    (BuyerInput(name="ก", address="x", branch="สาขาใหญ่"), "สาขา"),
])
def test_buyer_validation(db, owner, products, buyer, message):
    with pytest.raises(SaleError) as e:
        checkout(db, owner, [line(products["sand"], "คิว", "1")], buyer=buyer)
    assert message in str(e.value)


def test_buyer_branch_number(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")],
                    buyer=BuyerInput(name="ก", address="x", tax_id="0105555000005", branch="3"))
    assert sale.buyer_snapshot["branch_no"] == "00003"


def test_cashier_discount_needs_owner_pin(db, owner, cashier, products):
    lines = [line(products["tiger50"], "ถุง", "10", discount="5%")]
    with pytest.raises(SaleError) as e:
        checkout(db, cashier, lines)
    assert e.value.pin_failed
    db.rollback()
    with pytest.raises(SaleError):
        checkout(db, cashier, lines, owner_pin="9999")
    db.rollback()
    sale = checkout(db, cashier, lines, owner_pin=OWNER_PIN)
    assert (sale.lines[0].discount, sale.total) == (7250, 137750)
    log = db.scalars(select(AuditLog).where(AuditLog.action == "sale_create")).one()
    assert log.detail["discount_approved_by"] == owner.id


def test_owner_discount_needs_no_pin(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "2")], bill_discount="100")
    assert (sale.discount, sale.total) == (10000, 80000)


def test_cashier_without_discount_needs_no_pin(db, cashier, products):
    assert checkout(db, cashier, [line(products["sand"], "คิว", "1")]).total == 45000


def test_snapshot_survives_settings_change(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    s = get_shop_settings(db)
    s.shop_name = "ชื่อใหม่"
    products["sand"].name = "เปลี่ยนชื่อ"
    db.commit()
    db.refresh(sale)
    assert sale.shop_snapshot["name"] == ""
    assert sale.lines[0].product_name_snapshot == "ทรายหยาบ"


# --- void / reissue ------------------------------------------------------------


def test_void_restores_stock(db, owner, cashier, products):
    sale = checkout(db, cashier, [line(products["tiger50"], "พาเลท", "1")])
    assert stock.balance_of(db, products["tiger50"].id) == -40_000
    svc.void_sale(db, cashier, sale, "ลูกค้าเปลี่ยนใจ", OWNER_PIN)
    db.commit()
    assert sale.status == "voided" and sale.voided_by == owner.id
    assert stock.balance_of(db, products["tiger50"].id) == 0
    types = db.scalars(select(StockMovement.type).where(StockMovement.ref_id == sale.id)).all()
    assert sorted(types) == ["sale", "sale_void"]
    assert db.scalar(select(func.count()).where(AuditLog.action == "sale_void")) == 1


def test_void_rules(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    with pytest.raises(SaleError) as e:
        svc.void_sale(db, owner, sale, "ผิด", "0000")
    assert e.value.pin_failed
    with pytest.raises(SaleError):
        svc.void_sale(db, owner, sale, "  ", OWNER_PIN)
    svc.void_sale(db, owner, sale, "คีย์ผิด", OWNER_PIN)
    db.commit()
    with pytest.raises(SaleError):
        svc.void_sale(db, owner, sale, "คีย์ผิด", OWNER_PIN)


def test_reissue_links_replacement(db, owner, products):
    old = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    with pytest.raises(SaleError):
        checkout(db, owner, [line(products["sand"], "คิว", "2")], replaces_sale_id=old.id)  # not voided yet
    db.rollback()
    svc.void_sale(db, owner, old, "จำนวนผิด", OWNER_PIN)
    db.commit()
    new = checkout(db, owner, [line(products["sand"], "คิว", "2")], replaces_sale_id=old.id)
    db.refresh(old)
    assert old.replaced_by_sale_id == new.id
    with pytest.raises(SaleError):
        checkout(db, owner, [line(products["sand"], "คิว", "2")], replaces_sale_id=old.id)  # only once


def test_print_counts_and_copy_stamp(db, owner, products):
    sale = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    assert svc.record_print(db, owner, sale, "80mm") is False
    assert svc.record_print(db, owner, sale, "a4") is True
    db.commit()
    assert sale.print_count == 2
    assert db.scalar(select(func.count()).where(AuditLog.action == "sale_reprint")) == 1


def test_list_sales(db, owner, products):
    a = checkout(db, owner, [line(products["sand"], "คิว", "1")])
    checkout(db, owner, [line(products["tiger50"], "ถุง", "1")])
    svc.void_sale(db, owner, a, "ทดสอบ", OWNER_PIN)
    db.commit()
    assert svc.list_sales(db)[1] == 2
    assert [s.id for s in svc.list_sales(db, status="voided")[0]] == [a.id]
    assert [s.id for s in svc.list_sales(db, q="ทราย")[0]] == [a.id]
    assert svc.list_sales(db, q=a.doc_no)[0][0].id == a.id
