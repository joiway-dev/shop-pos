from sqlalchemy import select

from app.models import ProductAlias, Sale
from app.services import stock
from app.services.settings import get_shop_settings
from tests.conftest import CASHIER_PIN, OWNER_PIN, login
from tests.factories import seed_catalog


def unit_id(product, name):
    return next(u.id for u in product.units if u.unit_name == name)


def cart(p, unit="ถุง", qty="10", discount=""):
    return {"product_id": p.id, "unit_id": unit_id(p, unit), "qty": qty, "discount": discount}


def test_pos_page_and_grid(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.get("/pos")
    assert r.status_code == 200 and "ปูนซีเมนต์ตราเสือ 50 กก." in r.text
    r = client.get("/pos/products", params={"category_id": ""})
    assert "ทรายหยาบ" in r.text


def test_search_endpoint(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.get("/pos/search", params={"q": "PAL-TIGER50"}).json()
    assert r["status"] == "auto"
    assert r["candidates"][0]["unit_id"] == unit_id(p["tiger50"], "พาเลท")
    r = client.get("/pos/search", params={"q": "ปูนซีเมนต์ตราเสือ"}).json()
    assert r["status"] == "needs_choice" and len(r["candidates"]) >= 2


def test_parse_endpoint_offers_unit_matches(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.post("/pos/parse", json={"text": "ปูนซีเมนต์ตราเสือ 2 ถุง\nปูนเสือ 1 พาเลท"}).json()
    first, second = r["lines"]
    assert first["status"] == "needs_choice" and not r["ready"]
    opt = first["options"][str(p["tiger40"].id)]
    assert opt["unit_match_id"] == unit_id(p["tiger40"], "ถุง")
    assert second["status"] == "ok" and second["unit_id"] == unit_id(p["tiger50"], "พาเลท")


def test_quote_and_checkout(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    q = client.post("/pos/quote", json={"lines": [cart(p["tiger50"])], "bill_discount": ""}).json()
    assert q["ok"] and q["totals"]["total_text"] == "1,450.00"
    assert "สต็อกไม่พอ" in q["lines"][0]["stock_warning"]
    r = client.post("/pos/checkout", json={"lines": [cart(p["tiger50"])], "payment_type": "cash",
                                           "cash_received": "1500"}).json()
    assert r["ok"] and r["change_text"] == "50.00" and r["default_format"] == "80mm"
    db.rollback()
    assert db.get(Sale, r["sale_id"]).doc_no == r["doc_no"]
    assert stock.balance_of(db, p["tiger50"].id) == -10_000


def test_checkout_error_is_reported(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.post("/pos/checkout", json={"lines": [cart(p["sand"], "คิว", "1")], "cash_received": "10"}).json()
    assert r == {"ok": False, "error": "รับเงินไม่พอ", "pin_failed": False}


def test_cashier_discount_flow(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, cashier, CASHIER_PIN)
    body = {"lines": [cart(p["sand"], "คิว", "1", "10")], "payment_type": "transfer"}
    q = client.post("/pos/quote", json=body).json()
    assert q["needs_owner_pin"] is True
    assert client.post("/pos/checkout", json=body).json()["pin_failed"] is True
    r = client.post("/pos/checkout", json=dict(body, owner_pin=OWNER_PIN)).json()
    assert r["ok"] and r["total_text"] == "440.00"


def test_quote_hides_cost_from_everyone(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    q = client.post("/pos/quote", json={"lines": [cart(p["tiger50"])]}).text
    assert "cost" not in q


def test_learn_endpoint(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, cashier, CASHIER_PIN)
    r = client.post("/pos/learn", json={"text": "ปูนแดง", "product_id": p["tiger40"].id}).json()
    assert r["status"] == "saved"
    db.rollback()
    assert db.scalars(select(ProductAlias).where(ProductAlias.alias_text == "ปูนแดง")).one().source == "learned"


def _sell(client, p, **kw):
    body = {"lines": [cart(p["sand"], "คิว", "1")], "payment_type": "cash", **kw}
    return client.post("/pos/checkout", json=body).json()


def test_sales_pages_void_and_reissue(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, cashier, CASHIER_PIN)
    sold = _sell(client, p)
    assert sold["doc_no"] in client.get("/sales").text
    detail = client.get(f"/sales/{sold['sale_id']}")
    assert detail.status_code == 200 and "ทรายหยาบ" in detail.text

    r = client.post(f"/sales/{sold['sale_id']}/void", data={"reason": "ลูกค้ายกเลิก", "owner_pin": "0000"})
    assert r.status_code == 400 and "PIN" in r.text
    r = client.post(f"/sales/{sold['sale_id']}/void", data={"reason": "ลูกค้ายกเลิก", "owner_pin": OWNER_PIN},
                    follow_redirects=False)
    assert r.status_code == 303
    db.rollback()
    assert db.get(Sale, sold["sale_id"]).status == "voided"
    assert stock.balance_of(db, p["sand"].id) == 0

    re = client.get(f"/pos/reissue/{sold['sale_id']}").json()
    assert re["ok"] and re["lines"][0]["qty"] == "1"
    new = client.post("/pos/checkout", json={"lines": re["lines"], "payment_type": "cash",
                                             "replaces_sale_id": sold["sale_id"]}).json()
    assert new["ok"]
    assert client.get(f"/pos/reissue/{sold['sale_id']}").json()["ok"] is False


def test_print_receipt_and_copy_stamp(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    sold = _sell(client, p)
    first = client.get(f"/sales/{sold['sale_id']}/print", params={"format": "80mm", "auto": 0})
    assert first.status_code == 200 and "ใบเสร็จรับเงิน" in first.text
    assert "class=\"stamp stamp-copy\"" not in first.text
    # Not VAT registered: no tax wording anywhere (SPEC 1).
    assert "ภาษี" not in first.text
    second = client.get(f"/sales/{sold['sale_id']}/print", params={"format": "a4", "auto": 0})
    assert "class=\"stamp stamp-copy\"" in second.text and "ต้นฉบับ" not in second.text
    assert "สี่ร้อยห้าสิบบาทถ้วน" in second.text


def test_print_tax_invoice_a4(client, db, owner):
    p = seed_catalog(db, owner.id)
    s = get_shop_settings(db)
    s.vat_mode, s.tax_id, s.branch_no, s.shop_name = "vat", "0105555000005", "00000", "ร้านทดสอบ"
    db.commit()
    login(client, owner, OWNER_PIN)
    sold = _sell(client, p, payment_type="transfer",
                 buyer={"name": "บริษัท ก จำกัด", "address": "1 ถนน ก", "tax_id": "0105555000005", "branch": ""})
    assert sold["ok"] and sold["default_format"] == "a4"
    page = client.get(f"/sales/{sold['sale_id']}/print", params={"auto": 0}).text
    for text in ("ใบกำกับภาษี/ใบเสร็จรับเงิน", "ต้นฉบับ", "สำเนา", "เลขประจำตัวผู้เสียภาษี 0105555000005",
                 "บริษัท ก จำกัด", "สำนักงานใหญ่", "ภาษีมูลค่าเพิ่ม 7.00%", "29.44"):
        assert text in page, text


def test_print_voided_stamp(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    sold = _sell(client, p)
    client.post(f"/sales/{sold['sale_id']}/void", data={"reason": "ทดสอบยกเลิก", "owner_pin": OWNER_PIN})
    page = client.get(f"/sales/{sold['sale_id']}/print", params={"format": "80mm", "auto": 0}).text
    assert "class=\"stamp stamp-void\"" in page and "ทดสอบยกเลิก" in page


def test_pos_requires_login(client, owner):
    assert client.get("/pos", follow_redirects=False).status_code == 303
    assert client.post("/pos/quote", json={"lines": []}, headers={"HX-Request": "true"}).status_code == 401
