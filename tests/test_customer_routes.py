from sqlalchemy import select

from app.models import ArPayment, Customer, Quotation, Sale
from app.services import receivables
from tests.conftest import CASHIER_PIN, OWNER_PIN, login
from tests.factories import seed_catalog


def unit_id(product, name):
    return next(u.id for u in product.units if u.unit_name == name)


def line(p, unit, qty, discount=""):
    return {"product_id": p.id, "unit_id": unit_id(p, unit), "qty": qty, "discount": discount}


def make_customer(client, **extra):
    data = {"name": "ร้านช่างเอก", "phone": "081", "address": "1 ถ.สุขุมวิท", "credit_limit": "10000",
            "credit_days": "30", **extra}
    r = client.post("/customers", data=data, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2].split("?")[0])


def fresh(db):
    db.rollback()
    db.expire_all()


def test_customer_pages_and_credit_fields_owner_only(client, db, owner, cashier):
    login(client, owner, OWNER_PIN)
    cid = make_customer(client)
    assert "ร้านช่างเอก" in client.get("/customers").text
    assert client.get(f"/customers/{cid}").status_code == 200

    login(client, cashier, CASHIER_PIN)
    page = client.get(f"/customers/{cid}").text
    assert 'name="credit_limit"' not in page
    # Staff edits keep the owner's credit settings.
    client.post(f"/customers/{cid}", data={"name": "ร้านช่างเอก", "phone": "089", "credit_limit": "999999"})
    fresh(db)
    c = db.get(Customer, cid)
    assert (c.phone, c.credit_limit) == ("089", 1000000)


def test_pos_credit_sale_payment_and_statement(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    cid = make_customer(client)
    found = client.get("/pos/customers", params={"q": "ช่าง"}).json()
    assert found[0]["id"] == cid and found[0]["credit_limit_text"] == "10,000.00"

    body = {"lines": [line(p["tiger50"], "ถุง", "10")], "customer_id": cid}
    q = client.post("/pos/quote", json=body).json()
    assert q["customer"]["name"] == "ร้านช่างเอก" and q["over_limit"] is False
    r = client.post("/pos/checkout", json={**body, "payment_type": "credit"}).json()
    assert r["ok"] and r["doc_no"].startswith("DN") and r["default_format"] == "a4"

    detail = client.get(f"/sales/{r['sale_id']}").text
    assert "ครบกำหนด" in detail and "ค้าง 1,450.00" in detail
    printed = client.get(f"/sales/{r['sale_id']}/print", params={"auto": 0}).text
    assert "ใบส่งของ/ใบแจ้งหนี้" in printed and "ครบกำหนดชำระ" in printed

    assert "ร้านช่างเอก" in client.get("/ar").text
    assert client.get(f"/ar/pay/{cid}").status_code == 200
    bad = client.post(f"/ar/pay/{cid}", data={"amount": "99999", "method": "cash", "auto": "true"})
    assert bad.status_code == 400 and "รับเกินยอดค้าง" in bad.text
    ok = client.post(f"/ar/pay/{cid}", data={"amount": "1000", "method": "transfer", "auto": "true"},
                     follow_redirects=False)
    assert ok.status_code == 303
    pid = int(ok.headers["location"].split("/")[3].split("?")[0])
    assert "1,000.00" in client.get(f"/ar/payments/{pid}").text
    receipt = client.get(f"/ar/payments/{pid}/print", params={"auto": 0}).text
    assert "ใบรับเงิน" in receipt and "หนึ่งพันบาทถ้วน" in receipt
    statement = client.get(f"/customers/{cid}/statement", params={"auto": 0}).text
    assert "ใบแจ้งยอดค้างชำระ" in statement and "450.00" in statement

    # Void the payment (with confirmation form fields) -> bill is fully owed again.
    r2 = client.post(f"/ar/payments/{pid}/void", data={"reason": "รับผิด", "owner_pin": OWNER_PIN},
                     follow_redirects=False)
    assert r2.status_code == 303
    fresh(db)
    assert db.get(ArPayment, pid).status == "voided"
    assert receivables.customer_balance(db, cid) == 145000


def test_pay_with_manual_allocation(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    cid = make_customer(client)
    a = client.post("/pos/checkout", json={"lines": [line(p["sand"], "คิว", "1")], "customer_id": cid,
                                           "payment_type": "credit"}).json()
    b = client.post("/pos/checkout", json={"lines": [line(p["tiger50"], "ถุง", "2")], "customer_id": cid,
                                           "payment_type": "credit"}).json()
    r = client.post(f"/ar/pay/{cid}", data={"amount": "290", "method": "cash", f"alloc_{b['sale_id']}": "290"},
                    follow_redirects=False)
    assert r.status_code == 303
    fresh(db)
    assert [x.sale.id for x in receivables.open_bills(db, cid)] == [a["sale_id"]]


def test_cashier_over_limit_needs_pin_in_pos(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    cid = make_customer(client, credit_limit="100")
    login(client, cashier, CASHIER_PIN)
    body = {"lines": [line(p["tiger50"], "ถุง", "1")], "customer_id": cid}
    q = client.post("/pos/quote", json=body).json()
    assert q["over_limit"] and q["credit_pin"] and not q["discount_pin"]
    r = client.post("/pos/checkout", json={**body, "payment_type": "credit"}).json()
    assert r["pin_failed"] and "เกินวงเงิน" in r["error"]
    r = client.post("/pos/checkout", json={**body, "payment_type": "credit", "owner_pin": OWNER_PIN}).json()
    assert r["ok"]


def test_quotation_flow(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    cid = make_customer(client)
    assert client.get("/pos", params={"mode": "quote", "customer": cid}).status_code == 200
    r = client.post("/quotations", json={"lines": [line(p["tiger50"], "ถุง", "20", "5%")], "customer_id": cid,
                                         "valid_days": 10, "note": "รวมค่าส่ง"}).json()
    assert r["ok"] and r["doc_no"].startswith("QT")
    qid = r["id"]
    assert r["doc_no"] in client.get("/quotations").text
    detail = client.get(f"/quotations/{qid}").text
    assert "แปลงเป็นบิลขาย" in detail and "รวมค่าส่ง" in detail
    printed = client.get(f"/quotations/{qid}/print", params={"auto": 0}).text
    assert "ใบเสนอราคา" in printed and "ร้านช่างเอก" in printed and "ไม่ใช่ใบกำกับภาษี" in printed

    loaded = client.get(f"/pos/quotation/{qid}").json()
    assert loaded["state"] == "open" and loaded["lines"][0]["discount"] == "5%"
    edited = client.post(f"/quotations/{qid}/edit", json={"lines": [line(p["tiger50"], "ถุง", "30")],
                                                          "customer_id": cid}).json()
    assert edited["ok"] and edited["id"] == qid

    q = client.post("/pos/quote", json={"lines": [], "quotation_id": qid}).json()
    assert q["ok"] and q["totals"]["total_text"] == "4,350.00"
    sale = client.post("/pos/checkout", json={"lines": [], "quotation_id": qid, "payment_type": "transfer"}).json()
    assert sale["ok"]
    fresh(db)
    assert db.get(Quotation, qid).converted_sale_id == sale["sale_id"]
    assert db.get(Sale, sale["sale_id"]).customer_id == cid
    assert "แปลงเป็นบิลแล้ว" in client.get(f"/quotations/{qid}").text


def test_quotation_renew(client, db, owner):
    from datetime import timedelta

    from app.services.clock import now_local

    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.post("/quotations", json={"lines": [line(p["sand"], "คิว", "1")], "customer_name": "คุณสมศรี"}).json()
    fresh(db)
    qt = db.get(Quotation, r["id"])
    qt.valid_until = now_local().date() - timedelta(days=1)
    db.commit()
    assert "หมดอายุ" in client.get(f"/quotations/{r['id']}").text
    assert client.post("/pos/checkout", json={"lines": [], "quotation_id": r["id"]}).json()["ok"] is False
    assert client.post(f"/quotations/{r['id']}/renew", data={"days": "5"}, follow_redirects=False).status_code == 303
    fresh(db)
    assert db.get(Quotation, r["id"]).valid_until == now_local().date() + timedelta(days=5)


def test_sale_void_blocked_after_payment_page(client, db, owner):
    p = seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    cid = make_customer(client)
    s = client.post("/pos/checkout", json={"lines": [line(p["sand"], "คิว", "1")], "customer_id": cid,
                                           "payment_type": "credit"}).json()
    client.post(f"/ar/pay/{cid}", data={"amount": "450", "method": "cash", "auto": "true"})
    r = client.post(f"/sales/{s['sale_id']}/void", data={"reason": "คืนของ", "owner_pin": OWNER_PIN})
    assert r.status_code == 400 and "รับชำระแล้ว" in r.text
