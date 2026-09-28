import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Product
from app.services import auth, purchases, stock, suppliers
from app.services.purchases import PurchaseError, PurchaseInput, PurchaseLineInput
from app.services.sales import CartLineInput, CheckoutInput, create_sale
from app.services.settings import get_shop_settings
from tests.conftest import OWNER_PIN
from tests.factories import seed_catalog


@pytest.fixture
def products(db, owner):
    return seed_catalog(db, owner.id)


def unit_id(product, name):
    return next(u.id for u in product.units if u.unit_name == name)


def receive(db, actor, lines, **kw):
    db.commit()
    p = purchases.create_purchase(db, actor, PurchaseInput(lines=lines, **kw))
    db.commit()
    return p


def avg(db, product):
    db.expire(product)
    return db.get(Product, product.id).avg_cost


def test_owner_receives_with_cost(db, owner, products):
    tiger = products["tiger50"]
    sup = suppliers.save_supplier(db, owner.id, None, "บริษัท ปูนดี จำกัด")
    p = receive(db, owner, [PurchaseLineInput(tiger.id, unit_id(tiger, "พาเลท"), "2", "5,200")],
                supplier_id=sup.id, supplier_invoice_no="IV-001", supplier_invoice_date="2026-09-28")
    assert p.status == "completed" and p.doc_no.startswith("GR2569-") and p.doc_no.endswith("-0001")
    assert (p.subtotal, p.total, p.lines[0].cost_base) == (1040000, 1040000, 13000)
    assert stock.balance_of(db, tiger.id) == 80_000
    assert avg(db, tiger) == 13000
    assert db.scalar(select(func.count()).where(AuditLog.action == "purchase_create")) == 1


def test_moving_average_across_purchases(db, owner, products):
    sand = products["sand"]
    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "300")])
    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "340")])
    assert avg(db, sand) == 32000


def test_vat_registered_shop_costs_exclude_vat(db, owner, products):
    s = get_shop_settings(db)
    s.vat_mode, s.tax_id, s.branch_no = "vat", "0105555000005", "00000"
    sand = products["sand"]
    p = receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "1", "428")], vat_type="included")
    assert (p.vat_amount, p.total) == (2800, 42800)
    assert avg(db, sand) == 40000


def test_staff_receives_quantities_then_owner_enters_cost(db, owner, cashier, products):
    """Way A: staff with 'can receive stock' records quantities only."""
    sand = products["sand"]
    with pytest.raises(PurchaseError):
        receive(db, cashier, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "5")])
    db.rollback()
    auth.set_permissions(db, owner, cashier, can_receive_stock=True, can_see_cost=False)
    with pytest.raises(PurchaseError) as e:
        receive(db, cashier, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "5", "300")])
    assert "ต้นทุน" in str(e.value)
    db.rollback()

    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "300")])
    p = receive(db, cashier, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10")])
    assert p.status == "pending_cost" and p.total == 0
    assert stock.balance_of(db, sand.id) == 20_000  # stock goes up right away
    assert avg(db, sand) == 30000  # unchanged until the cost is known
    assert purchases.pending_cost_count(db) == 1

    with pytest.raises(PurchaseError):
        purchases.enter_costs(db, cashier, p, {p.lines[0].id: "360"}, "none")
    purchases.enter_costs(db, owner, p, {p.lines[0].id: "360"}, "none")
    db.commit()
    assert p.status == "completed" and p.costed_by == owner.id and p.total == 360000
    assert avg(db, sand) == 33000
    assert purchases.pending_cost_count(db) == 0


def test_staff_with_cost_permission_can_enter_cost(db, owner, cashier, products):
    """Way B: owner lets a staff member see and enter costs."""
    sand = products["sand"]
    auth.set_permissions(db, owner, cashier, can_receive_stock=True, can_see_cost=True)
    p = receive(db, cashier, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "2", "310")])
    assert p.status == "completed"


def test_cost_entered_late_is_replayed_in_order(db, owner, cashier, products):
    sand = products["sand"]
    auth.set_permissions(db, owner, cashier, True, False)
    pending = receive(db, cashier, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10")])
    db.commit()
    create_sale(db, owner, CheckoutInput([CartLineInput(sand.id, unit_id(sand, "คิว"), "10")]))
    db.commit()
    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "400")])
    assert avg(db, sand) == 40000  # stock was 0 before this purchase
    purchases.enter_costs(db, owner, pending, {pending.lines[0].id: "300"}, "none")
    db.commit()
    assert avg(db, sand) == 40000  # the 10 @300 were sold before the 400 purchase


def test_partial_costs_rejected(db, owner, products):
    sand, tiger = products["sand"], products["tiger50"]
    with pytest.raises(PurchaseError):
        receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "1", "300"),
                            PurchaseLineInput(tiger.id, unit_id(tiger, "ถุง"), "1", "")])


@pytest.mark.parametrize("line,message", [
    (lambda p: PurchaseLineInput(p["sand"].id, unit_id(p["sand"], "คิว"), "0", "1"), "จำนวน"),
    (lambda p: PurchaseLineInput(p["sand"].id, unit_id(p["tiger50"], "ถุง"), "1", "1"), "หน่วย"),
    (lambda p: PurchaseLineInput(p["sand"].id, unit_id(p["sand"], "คิว"), "1", "-5"), "ราคาทุน"),
])
def test_line_validation(db, owner, products, line, message):
    with pytest.raises(PurchaseError) as e:
        receive(db, owner, [line(products)])
    assert message in str(e.value)


def test_void_purchase_takes_stock_back_and_replays_cost(db, owner, products):
    sand = products["sand"]
    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "300")])
    wrong = receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "3000")])
    assert avg(db, sand) == 165000
    with pytest.raises(PurchaseError) as e:
        purchases.void_purchase(db, owner, wrong, "ใส่ราคาผิด", "0000")
    assert e.value.pin_failed
    purchases.void_purchase(db, owner, wrong, "ใส่ราคาผิด", OWNER_PIN)
    db.commit()
    assert wrong.status == "voided"
    assert stock.balance_of(db, sand.id) == 10_000
    assert avg(db, sand) == 30000
    with pytest.raises(PurchaseError):
        purchases.void_purchase(db, owner, wrong, "ซ้ำ", OWNER_PIN)


def test_sale_uses_current_avg_cost_snapshot(db, owner, products):
    sand = products["sand"]
    receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "10", "300")])
    sale = create_sale(db, owner, CheckoutInput([CartLineInput(sand.id, unit_id(sand, "คิว"), "1")]))
    db.commit()
    assert sale.lines[0].unit_cost_snapshot == 30000


def test_list_purchases(db, owner, products):
    sup = suppliers.save_supplier(db, owner.id, None, "ร้านทรายทอง")
    sand = products["sand"]
    p = receive(db, owner, [PurchaseLineInput(sand.id, unit_id(sand, "คิว"), "1", "300")], supplier_id=sup.id)
    assert [x.id for x in purchases.list_purchases(db, q="ทรายทอง")[0]] == [p.id]
    assert [x.id for x in purchases.list_purchases(db, q="ทรายหยาบ")[0]] == [p.id]
    assert purchases.list_purchases(db, status="voided")[1] == 0


def test_suppliers(db, owner):
    s = suppliers.save_supplier(db, owner.id, None, "  ปูนดี  ", tax_id="0105555000005")
    assert (s.name, s.tax_id) == ("ปูนดี", "0105555000005")
    with pytest.raises(suppliers.SupplierError):
        suppliers.save_supplier(db, owner.id, None, "ปูนดี")
    with pytest.raises(suppliers.SupplierError):
        suppliers.save_supplier(db, owner.id, None, "อื่น", tax_id="123")
    suppliers.save_supplier(db, owner.id, s, "ปูนดีมาก", phone="02")
    suppliers.set_supplier_active(db, owner.id, s, False)
    assert s.name == "ปูนดีมาก" and not s.is_active
