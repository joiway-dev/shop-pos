from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Sale
from app.services import customers, quotations, receivables, stock
from app.services.clock import now_local
from app.services.customers import CustomerError, CustomerInput
from app.services.quotations import QuotationError, QuotationInput
from app.services.receivables import ReceivableError
from app.services.sales import CartLineInput, CheckoutInput, SaleError, create_sale, void_sale
from app.services.settings import get_shop_settings
from tests.conftest import OWNER_PIN
from tests.factories import seed_catalog


@pytest.fixture
def products(db, owner):
    return seed_catalog(db, owner.id)


@pytest.fixture
def cust(db, owner):
    c = customers.save_customer(db, owner, None, CustomerInput(
        "ร้านช่างเอก", phone="081", address="1 ถ.สุขุมวิท", credit_limit="10000", credit_days="30"))
    db.commit()
    return c


def unit(product, name):
    return next(u.id for u in product.units if u.unit_name == name)


def sell_credit(db, actor, cust, product, unit_name, qty, **kw):
    db.commit()
    sale = create_sale(db, actor, CheckoutInput(
        [CartLineInput(product.id, unit(product, unit_name), qty)], payment_type="credit", customer_id=cust.id, **kw))
    db.commit()
    return sale


def backdate(db, sale, days):
    sale.created_at = sale.created_at - timedelta(days=days)
    db.commit()


# --- customers -----------------------------------------------------------------


def test_customer_create_and_owner_only_credit(db, owner, cashier):
    c = customers.save_customer(db, cashier, None, CustomerInput("  ลูกค้า  ก ", tax_id="0105555000005"))
    assert (c.name, c.branch, c.credit_limit) == ("ลูกค้า ก", "00000", 0)
    with pytest.raises(CustomerError):
        customers.save_customer(db, cashier, c, CustomerInput("ลูกค้า ก", credit_limit="5000"))
    customers.save_customer(db, owner, c, CustomerInput("ลูกค้า ก", credit_limit="5,000", credit_days="15"))
    assert (c.credit_limit, c.credit_days) == (500000, 15)
    with pytest.raises(CustomerError):
        customers.save_customer(db, owner, None, CustomerInput(""))
    with pytest.raises(CustomerError):
        customers.save_customer(db, owner, None, CustomerInput("x", tax_id="123"))
    assert [x.id for x in customers.search_customers(db, "ลูกค้า")] == [c.id]


# --- credit sales --------------------------------------------------------------


def test_credit_sale_creates_dn_with_due_date(db, owner, cust, products):
    sale = sell_credit(db, owner, cust, products["tiger50"], "ถุง", "10")
    assert sale.doc_type == "DN" and sale.doc_no.startswith("DN")
    assert sale.customer_id == cust.id and sale.cash_received is None
    assert sale.due_date == now_local().date() + timedelta(days=30)
    assert sale.buyer_snapshot["name"] == "ร้านช่างเอก"
    assert receivables.customer_balance(db, cust.id) == 145000


def test_credit_needs_customer(db, owner, products):
    with pytest.raises(SaleError):
        create_sale(db, owner, CheckoutInput([CartLineInput(products["sand"].id, unit(products["sand"], "คิว"), "1")],
                                             payment_type="credit"))


def test_over_limit_needs_owner_pin_for_staff(db, owner, cashier, cust, products):
    sell_credit(db, cashier, cust, products["tiger50"], "ถุง", "60")  # 8,700 within 10,000
    with pytest.raises(SaleError) as e:
        sell_credit(db, cashier, cust, products["tiger50"], "ถุง", "10")  # would be 10,150
    assert e.value.pin_failed and "เกินวงเงิน" in str(e.value)
    db.rollback()
    sale = sell_credit(db, cashier, cust, products["tiger50"], "ถุง", "10", owner_pin=OWNER_PIN)
    log = db.scalars(select(AuditLog).where(AuditLog.entity_id == sale.id, AuditLog.action == "sale_create")).one()
    assert log.detail["over_credit_limit"] is True and log.detail["approved_by"] == owner.id
    # The owner does not need to type a PIN for their own over-limit sale.
    sell_credit(db, owner, cust, products["sand"], "คิว", "1")


def test_zero_limit_means_every_credit_sale_needs_pin(db, owner, cashier, products):
    c = customers.save_customer(db, owner, None, CustomerInput("ไม่มีเครดิต", address="x"))
    with pytest.raises(SaleError):
        sell_credit(db, cashier, c, products["sand"], "คิว", "1")


def test_cash_sale_with_customer_fills_buyer(db, owner, cust, products):
    db.commit()
    sale = create_sale(db, owner, CheckoutInput([CartLineInput(products["sand"].id, unit(products["sand"], "คิว"), "1")],
                                                customer_id=cust.id))
    assert sale.doc_type == "RC" and sale.buyer_snapshot["name"] == "ร้านช่างเอก"
    assert receivables.customer_balance(db, cust.id) == 0  # cash sales are not receivables


def test_vat_shop_credit_sale_needs_customer_address(db, owner, products):
    s = get_shop_settings(db)
    s.vat_mode, s.tax_id, s.branch_no = "vat", "0105555000005", "00000"
    c = customers.save_customer(db, owner, None, CustomerInput("ไม่มีที่อยู่", credit_limit="99999"))
    with pytest.raises(SaleError) as e:
        sell_credit(db, owner, c, products["sand"], "คิว", "1")
    assert "ที่อยู่" in str(e.value)


# --- payments / allocations ------------------------------------------------------


def test_partial_payment_oldest_first(db, owner, cust, products):
    a = sell_credit(db, owner, cust, products["sand"], "คิว", "1")      # 450
    b = sell_credit(db, owner, cust, products["tiger50"], "ถุง", "2")   # 290
    p = receivables.receive_payment(db, owner, cust, "500", "cash")
    db.commit()
    assert p.doc_no.startswith("RV")
    assert [(x.sale_id, x.amount) for x in p.allocations] == [(a.id, 45000), (b.id, 5000)]
    bills = receivables.open_bills(db, cust.id)
    assert [(x.sale.id, x.outstanding) for x in bills] == [(b.id, 24000)]
    assert receivables.customer_balance(db, cust.id) == 24000


def test_payment_across_several_bills_with_chosen_allocation(db, owner, cust, products):
    a = sell_credit(db, owner, cust, products["sand"], "คิว", "1")      # 450
    b = sell_credit(db, owner, cust, products["tiger50"], "ถุง", "2")   # 290
    c = sell_credit(db, owner, cust, products["rebar9"], "เส้น", "1")   # 105
    p = receivables.receive_payment(db, owner, cust, "395", "transfer", allocations={b.id: "290", c.id: "105"})
    db.commit()
    assert sorted((x.sale_id, x.amount) for x in p.allocations) == [(b.id, 29000), (c.id, 10500)]
    assert [x.sale.id for x in receivables.open_bills(db, cust.id)] == [a.id]
    # Pay the rest in two parts.
    receivables.receive_payment(db, owner, cust, "200", "cash")
    db.commit()
    receivables.receive_payment(db, owner, cust, "250", "cheque", note="เช็ค 123456")
    db.commit()
    assert receivables.customer_balance(db, cust.id) == 0


@pytest.mark.parametrize("amount,alloc,message", [
    ("0", None, "มากกว่า 0"),
    ("10000", None, "รับเกินยอดค้าง"),
    ("100", "200", "เกินยอดค้าง"),
    ("100", "50", "เท่ากับยอดรับชำระ"),
])
def test_payment_validation(db, owner, cust, products, amount, alloc, message):
    s = sell_credit(db, owner, cust, products["tiger50"], "ถุง", "1")  # 145
    allocations = {s.id: alloc} if alloc else None
    with pytest.raises(ReceivableError) as e:
        receivables.receive_payment(db, owner, cust, amount, "cash", allocations=allocations)
    assert message in str(e.value)


def test_no_open_bills(db, owner, cust):
    with pytest.raises(ReceivableError):
        receivables.receive_payment(db, owner, cust, "1", "cash")


def test_void_payment_reopens_bills_and_blocks_sale_void(db, owner, cust, products):
    s = sell_credit(db, owner, cust, products["sand"], "คิว", "1")
    p = receivables.receive_payment(db, owner, cust, "450", "cash")
    db.commit()
    assert receivables.customer_balance(db, cust.id) == 0
    with pytest.raises(SaleError) as e:
        void_sale(db, owner, s, "ลูกค้าคืนของ", OWNER_PIN)
    assert "รับชำระแล้ว" in str(e.value)
    with pytest.raises(ReceivableError) as e:
        receivables.void_payment(db, owner, p, "รับผิด", "0000")
    assert e.value.pin_failed
    receivables.void_payment(db, owner, p, "รับผิด", OWNER_PIN)
    db.commit()
    assert receivables.customer_balance(db, cust.id) == 45000
    void_sale(db, owner, s, "ลูกค้าคืนของ", OWNER_PIN)
    db.commit()
    assert receivables.customer_balance(db, cust.id) == 0  # voided sales are not owed
    assert db.scalar(select(func.count()).where(AuditLog.action == "ar_payment_void")) == 1


def test_aging_buckets_and_overdue(db, owner, cust, products):
    fresh = sell_credit(db, owner, cust, products["sand"], "คิว", "1")      # 450, today
    mid = sell_credit(db, owner, cust, products["tiger50"], "ถุง", "2")     # 290
    old = sell_credit(db, owner, cust, products["rebar9"], "เส้น", "1")     # 105
    backdate(db, mid, 45)
    backdate(db, old, 100)
    old.due_date = now_local().date() - timedelta(days=70)
    db.commit()
    rows = receivables.aging(db)
    assert len(rows) == 1
    assert rows[0].buckets == [45000, 29000, 0, 10500]
    assert rows[0].total == 84500 and rows[0].overdue == 10500


# --- quotations ------------------------------------------------------------------


def qt_input(products, **kw):
    lines = [CartLineInput(products["tiger50"].id, unit(products["tiger50"], "ถุง"), "20", "5%"),
             CartLineInput(products["sand"].id, unit(products["sand"], "คิว"), "2")]
    return QuotationInput(lines=lines, **kw)


def test_quotation_create_edit_and_convert_at_quoted_price(db, owner, cust, products):
    db.commit()
    qt = quotations.save_quotation(db, owner, None, qt_input(products, customer_id=cust.id, valid_days=10))
    db.commit()
    assert qt.doc_no.startswith("QT") and qt.total == 275500 + 90000
    assert qt.valid_until == now_local().date() + timedelta(days=10)
    quotations.save_quotation(db, owner, qt, qt_input(products, customer_id=cust.id, bill_discount="500"))
    db.commit()
    assert qt.total == 315500

    products["tiger50"].units[0].price = 99999  # price goes up after quoting
    db.commit()
    sale = create_sale(db, owner, CheckoutInput([], quotation_id=qt.id, payment_type="credit"))
    db.commit()
    assert sale.total == 315500 and sale.customer_id == cust.id
    assert qt.status == "converted" and qt.converted_sale_id == sale.id
    assert stock.balance_of(db, products["tiger50"].id) == -20_000
    with pytest.raises(QuotationError):
        quotations.save_quotation(db, owner, qt, qt_input(products))
    db.rollback()
    with pytest.raises(SaleError):
        create_sale(db, owner, CheckoutInput([], quotation_id=qt.id))


def test_expired_quotation_must_be_renewed(db, owner, products):
    db.commit()
    qt = quotations.save_quotation(db, owner, None, qt_input(products, customer_name="คุณสมศรี"))
    qt.valid_until = now_local().date() - timedelta(days=1)
    db.commit()
    assert quotations.status_of(qt) == "expired"
    with pytest.raises(SaleError) as e:
        create_sale(db, owner, CheckoutInput([], quotation_id=qt.id))
    assert "หมดอายุ" in str(e.value)
    db.rollback()
    quotations.renew(db, owner, qt, 7)
    db.commit()
    assert quotations.status_of(qt) == "open"
    assert create_sale(db, owner, CheckoutInput([], quotation_id=qt.id)).doc_type == "RC"


def test_staff_quotation_discount_needs_pin(db, owner, cashier, products):
    db.commit()
    with pytest.raises(QuotationError) as e:
        quotations.save_quotation(db, cashier, None, qt_input(products))
    assert e.value.pin_failed
    db.rollback()
    qt = quotations.save_quotation(db, cashier, None, qt_input(products, owner_pin=OWNER_PIN))
    db.commit()
    # Converting a quotation needs no PIN again.
    assert create_sale(db, cashier, CheckoutInput([], quotation_id=qt.id)).total == qt.total


def test_list_quotations(db, owner, products):
    db.commit()
    a = quotations.save_quotation(db, owner, None, qt_input(products, customer_name="คุณสมศรี"))
    db.commit()
    assert quotations.list_quotations(db, q="สมศรี")[0][0].id == a.id
    assert quotations.list_quotations(db, status="open")[1] == 1
    assert quotations.list_quotations(db, status="expired")[1] == 0


def test_credit_sale_numbers_do_not_clash_with_cash(db, owner, cust, products):
    cash = create_sale(db, owner, CheckoutInput([CartLineInput(products["sand"].id, unit(products["sand"], "คิว"), "1")]))
    db.commit()
    credit = sell_credit(db, owner, cust, products["sand"], "คิว", "1")
    assert cash.doc_no.startswith("RC") and credit.doc_no.startswith("DN")
    assert db.scalar(select(func.count()).select_from(Sale)) == 2
