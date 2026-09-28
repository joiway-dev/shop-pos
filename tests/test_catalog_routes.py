from sqlalchemy import select

from app.models import Product, ProductAlias
from app.services import product_import
from tests.conftest import CASHIER_PIN, OWNER_PIN, login
from tests.factories import seed_catalog

PRODUCT_FORM = {
    "name": "ปูนซีเมนต์ตราเสือ 50 กก.",
    "base_unit": "ถุง",
    "base_price": "145",
    "default_unit": "0",
    "unit_name": ["พาเลท"],
    "unit_factor": ["40"],
    "unit_price": ["5600"],
    "unit_barcode": [""],
}


def test_owner_creates_and_edits_product(client, db, owner):
    login(client, owner, OWNER_PIN)
    assert client.get("/products/new").status_code == 200
    r = client.post("/products", data=PRODUCT_FORM, follow_redirects=False)
    assert r.status_code == 303, r.text
    p = db.scalars(select(Product)).one()
    assert p.default_unit.unit_name == "พาเลท"

    page = client.get(f"/products/{p.id}")
    assert page.status_code == 200 and "พาเลท" in page.text

    form = dict(PRODUCT_FORM, base_price="150", default_unit="base", unit_name=[], unit_factor=[],
                unit_price=[], unit_barcode=[])
    r = client.post(f"/products/{p.id}", data=form, follow_redirects=False)
    assert r.status_code == 303
    db.rollback()  # end the read snapshot so we see commits made by the app
    p = db.get(Product, p.id)
    assert [(u.unit_name, u.price) for u in p.units] == [("ถุง", 15000)]


def test_product_form_errors_are_shown(client, owner):
    login(client, owner, OWNER_PIN)
    r = client.post("/products", data=dict(PRODUCT_FORM, base_price="abc", unit_factor=["0"]))
    assert r.status_code == 400
    assert "ราคาหน่วยฐานต้องเป็นตัวเลข" in r.text
    assert "หน่วยเพิ่มแถวที่ 1" in r.text


def test_cashier_sees_products_without_cost_and_cannot_edit(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, cashier, CASHIER_PIN)
    r = client.get("/products")
    assert r.status_code == 200 and "ปูนซีเมนต์ตราเสือ" in r.text
    assert "ต้นทุน" not in r.text
    detail = client.get(f"/products/{p['tiger50'].id}")
    assert detail.status_code == 200 and "ต้นทุน" not in detail.text and "<form" not in detail.text.split("<main")[1]
    assert client.get("/products/new").status_code == 403
    assert client.post("/products", data=PRODUCT_FORM).status_code == 403
    assert client.post(f"/products/{p['tiger50'].id}/active", data={"active": "false"}).status_code == 403
    assert client.get("/products/import").status_code == 403
    assert client.get("/categories").status_code == 403
    assert client.get("/aliases").status_code == 403


def test_product_list_search(client, db, owner):
    seed_catalog(db, owner.id)
    login(client, owner, OWNER_PIN)
    r = client.get("/products", params={"q": "ปูนเสือ"})
    assert "ตราเสือ 50" in r.text and "ทรายหยาบ" not in r.text


def test_alias_add_and_delete_routes(client, db, owner):
    p = seed_catalog(db, owner.id)["sand"]
    login(client, owner, OWNER_PIN)
    r = client.post(f"/products/{p.id}/aliases", data={"alias": "ทรายถม"}, follow_redirects=False)
    assert r.status_code == 303
    alias = db.scalars(select(ProductAlias).where(ProductAlias.alias_text == "ทรายถม")).one()
    assert "ทรายถม" in client.get("/aliases").text
    alias_id = alias.id
    r = client.post(f"/aliases/{alias_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    db.rollback()  # end the read snapshot so we see commits made by the app
    assert db.get(ProductAlias, alias_id) is None


def test_categories_routes(client, owner):
    login(client, owner, OWNER_PIN)
    assert client.post("/categories", data={"name": "ปูน"}, follow_redirects=False).status_code == 303
    r = client.post("/categories", data={"name": "ปูน"})
    assert r.status_code == 400 and "มีหมวดหมู่ชื่อนี้แล้ว" in r.text


def test_import_flow(client, db, owner):
    login(client, owner, OWNER_PIN)
    assert client.get("/products/import/template.xlsx").status_code == 200
    assert client.get("/products/import/template.csv").content.startswith(b"\xef\xbb\xbf")
    r = client.post("/products/import", files={"file": ("p.csv", product_import.template_csv(), "text/csv")})
    assert r.status_code == 200 and "เพิ่มใหม่ <b>3</b>" in r.text
    assert db.scalar(select(Product.id)) is None  # preview only
    db.rollback()
    token = r.text.split('name="token" value="')[1].split('"')[0]
    r = client.post("/products/import/confirm", data={"token": token, "ext": "csv"})
    assert "นำเข้าเรียบร้อย" in r.text
    assert len(db.scalars(select(Product)).all()) == 3
    # Token is single-use.
    r = client.post("/products/import/confirm", data={"token": token, "ext": "csv"})
    assert r.status_code == 400


def test_import_bad_file(client, owner):
    login(client, owner, OWNER_PIN)
    r = client.post("/products/import", files={"file": ("p.txt", b"x", "text/plain")})
    assert r.status_code == 400 and "xlsx" in r.text


def test_import_confirm_rejects_bad_token(client, owner):
    login(client, owner, OWNER_PIN)
    assert client.post("/products/import/confirm", data={"token": "../../x", "ext": "csv"}).status_code == 400


def test_matching_pages(client, db, owner, cashier):
    p = seed_catalog(db, owner.id)
    login(client, cashier, CASHIER_PIN)
    assert client.get("/matching").status_code == 200
    r = client.post("/matching/search", data={"query": "ปูนเสือ"})
    assert "ลงบิลได้ทันที" in r.text
    r = client.post("/matching/parse", data={"text": "สวัสดีค่ะ\nเหล็ก 12 มิล 20 เส้น\nทรายหยาบ 3 ถุง"})
    assert "เหล็กเส้นกลม 12 มม." in r.text and "ข้าม" in r.text and "ต้องเลือก" in r.text
    assert "disabled" in r.text.split("ลงบิล</button>")[0].rsplit("<button", 1)[1]
    r = client.post("/matching/learn", data={"query": "ปูนถุงแดง", "product_id": p["tiger40"].id, "remember": "true"})
    assert "จำไว้แล้ว" in r.text
    alias = db.scalars(select(ProductAlias).where(ProductAlias.alias_text == "ปูนถุงแดง")).one()
    assert alias.source == "learned"


def test_settings_match_thresholds(client, db, owner):
    login(client, owner, OWNER_PIN)
    data = {"shop_name": "ร้าน", "vat_mode": "none", "backup_keep_days": "30",
            "match_auto_accept": "90", "match_suggest_min": "95", "match_min_gap": "8"}
    r = client.post("/settings", data=data)
    assert r.status_code == 400 and "ต้องน้อยกว่า" in r.text
    r = client.post("/settings", data=dict(data, match_suggest_min="50"), follow_redirects=False)
    assert r.status_code == 303
