import pytest
from sqlalchemy import select

from app.models import AuditLog, ProductUnit
from app.services import catalog
from app.services.catalog import CatalogError, ProductInput, UnitInput
from tests.factories import make_product


def base_input(**kw) -> ProductInput:
    data = ProductInput(name="ปูนซีเมนต์ตราเสือ 50 กก.", base_unit="ถุง", base_price="145")
    for k, v in kw.items():
        setattr(data, k, v)
    return data


def audit_actions(db, entity_id=None):
    stmt = select(AuditLog.action)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return db.scalars(stmt).all()


# --- categories ----------------------------------------------------------------


def test_category_crud(db, owner):
    cat = catalog.create_category(db, owner.id, "  ปูน  ")
    assert cat.name == "ปูน"
    with pytest.raises(CatalogError):
        catalog.create_category(db, owner.id, "ปูน")
    catalog.rename_category(db, owner.id, cat, "ปูนซีเมนต์")
    catalog.set_category_active(db, owner.id, cat, False)
    assert not cat.is_active
    assert {"category_create", "category_update", "category_deactivate"} <= set(audit_actions(db))


def test_get_or_create_category_is_case_insensitive(db, owner):
    a = catalog.get_or_create_category(db, owner.id, "PVC")
    assert catalog.get_or_create_category(db, owner.id, "pvc").id == a.id


# --- products ------------------------------------------------------------------


def test_create_product_with_units(db, owner):
    p = catalog.create_product(
        db, owner.id,
        base_input(units=[UnitInput("พาเลท", "40", "5,600")], default_unit="พาเลท"),
    )
    db.commit()
    assert p.sku == "P00001"
    assert p.name_normalized == "ปูนซีเมนต์ตราเสือ 50 กก"
    units = {u.unit_name: u for u in p.units}
    assert (units["ถุง"].factor_to_base, units["ถุง"].price) == (1000, 14500)
    assert (units["พาเลท"].factor_to_base, units["พาเลท"].price) == (40000, 560000)
    assert p.default_unit.unit_name == "พาเลท"
    assert p.base_unit_row.unit_name == "ถุง"
    assert "product_create" in audit_actions(db, p.id)


def test_fractional_factor(db, owner):
    p = catalog.create_product(db, owner.id, base_input(name="สายไฟ", base_unit="เมตร", base_price="12",
                                                        units=[UnitInput("ฟุต", "0.305", "4")]))
    assert next(u for u in p.units if u.unit_name == "ฟุต").factor_to_base == 305


def test_auto_sku_increments(db, owner):
    make_product(db, owner.id, "ก", "ชิ้น", "1")
    make_product(db, owner.id, "ข", "ชิ้น", "1", sku="CUSTOM-1")
    assert make_product(db, owner.id, "ค", "ชิ้น", "1").sku == "P00002"


@pytest.mark.parametrize(
    "kw,field",
    [
        ({"name": " "}, "name"),
        ({"name": "---"}, "name"),
        ({"base_unit": ""}, "base_unit"),
        ({"base_price": "abc"}, "base_price"),
        ({"base_price": "-5"}, "base_price"),
        ({"base_price": "1.234"}, "base_price"),
        ({"min_stock": "-1"}, "min_stock"),
        ({"category_id": 999}, "category_id"),
        ({"units": [UnitInput("พาเลท", "0", "10")]}, "units"),
        ({"units": [UnitInput("", "40", "10")]}, "units"),
        ({"units": [UnitInput("ถุง", "40", "10")]}, "units"),  # same as base unit
        ({"units": [UnitInput("กล่อง", "10", "1"), UnitInput("กล่อง", "20", "2")]}, "units"),
        ({"default_unit": "ลัง"}, "units"),
        ({"barcode": "111", "units": [UnitInput("พาเลท", "40", "10", "111")]}, "barcode"),
    ],
)
def test_product_validation(db, owner, kw, field):
    with pytest.raises(CatalogError) as e:
        catalog.create_product(db, owner.id, base_input(**kw))
    assert field in e.value.errors


def test_unit_names_compared_canonically(db, owner):
    with pytest.raises(CatalogError) as e:
        catalog.create_product(db, owner.id, base_input(base_unit="เมตร", units=[UnitInput("ม.", "1", "1")]))
    assert "units" in e.value.errors


def test_sku_and_barcode_unique_across_catalog(db, owner):
    make_product(db, owner.id, "ก", "ชิ้น", "1", sku="A1", barcode="885000",
                 units=[("กล่อง", "10", "9", "885001")])
    with pytest.raises(CatalogError) as e:
        catalog.create_product(db, owner.id, base_input(sku="A1"))
    assert "sku" in e.value.errors
    for code in ("885000", "885001"):
        with pytest.raises(CatalogError) as e:
            catalog.create_product(db, owner.id, base_input(barcode=code))
        assert "barcode" in e.value.errors
        with pytest.raises(CatalogError):
            catalog.create_product(db, owner.id, base_input(units=[UnitInput("ลัง", "5", "5", code)]))


def test_update_product_prices_audited(db, owner):
    p = make_product(db, owner.id, "ปูน", "ถุง", "145", units=[("พาเลท", "40", "5600")])
    db.commit()
    catalog.update_product(db, owner.id, p, ProductInput(
        sku=p.sku, name="ปูนเสือ", base_unit="ถุง", base_price="150",
        units=[UnitInput("พาเลท", "40", "5800")],
    ))
    db.commit()
    assert p.name == "ปูนเสือ"
    assert {u.unit_name: u.price for u in p.units} == {"ถุง": 15000, "พาเลท": 580000}
    log = db.scalars(select(AuditLog).where(AuditLog.action == "price_change")).one()
    assert log.detail["prices"] == [
        {"unit": "ถุง", "old": "145.00", "new": "150.00"},
        {"unit": "พาเลท", "old": "5,600.00", "new": "5,800.00"},
    ]


def test_update_adds_removes_and_renames_units(db, owner):
    p = make_product(db, owner.id, "สายไฟ", "เมตร", "12", units=[("ม้วน", "100", "1100")])
    db.commit()
    base_id = p.base_unit_row.id
    catalog.update_product(db, owner.id, p, ProductInput(
        sku=p.sku, name="สายไฟ", base_unit="ม.", base_price="12",
        units=[UnitInput("ขด", "50", "560")],
    ))
    db.commit()
    assert sorted(u.unit_name for u in p.units) == ["ขด", "ม."]
    assert p.base_unit_row.id == base_id  # base row kept, renamed
    assert db.scalar(select(ProductUnit).where(ProductUnit.unit_name == "ม้วน")) is None


def test_update_can_reuse_removed_unit_name_as_new_row(db, owner):
    p = make_product(db, owner.id, "สายไฟ", "เมตร", "12", units=[("ม้วน", "100", "1100")])
    db.commit()
    catalog.update_product(db, owner.id, p, ProductInput(
        sku=p.sku, name="สายไฟ", base_unit="เมตร", base_price="12", units=[UnitInput("ม้วน", "90", "1000")],
    ))
    db.commit()
    assert next(u for u in p.units if u.unit_name == "ม้วน").factor_to_base == 90000


def test_deactivate_product(db, owner):
    p = make_product(db, owner.id, "ปูน", "ถุง", "145")
    catalog.set_product_active(db, owner.id, p, False)
    assert not p.is_active
    assert "product_deactivate" in audit_actions(db, p.id)


def test_search_products(db, owner):
    a = make_product(db, owner.id, "ปูนซีเมนต์ตราเสือ", "ถุง", "145", barcode="885123", aliases=["ปูนเสือ"])
    b = make_product(db, owner.id, "ทรายหยาบ", "คิว", "450", units=[("ถุง", "0.02", "15", "885999")])
    catalog.set_product_active(db, owner.id, b, False)
    db.commit()
    assert catalog.search_products(db, "ตรา เสือ")[0] == [a]
    assert catalog.search_products(db, "ปูนเสือ")[0] == [a]  # via alias
    assert catalog.search_products(db, "885123")[0] == [a]
    assert catalog.search_products(db, a.sku)[0] == [a]
    assert catalog.search_products(db, "ทราย")[1] == 0
    assert catalog.search_products(db, "885999", include_inactive=True)[0] == [b]


# --- aliases -------------------------------------------------------------------


def test_add_alias(db, owner):
    p = make_product(db, owner.id, "ปูนซีเมนต์ตราเสือ", "ถุง", "145")
    alias, warning = catalog.add_alias(db, owner.id, p, "ปูน เสือ")
    assert (alias.alias_normalized, alias.source, warning) == ("ปูนเสือ", "manual", None)
    with pytest.raises(CatalogError):
        catalog.add_alias(db, owner.id, p, "ปูนเสือ")  # duplicate on same product
    with pytest.raises(CatalogError):
        catalog.add_alias(db, owner.id, p, "ปูนซีเมนต์ ตราเสือ")  # same as name
    with pytest.raises(CatalogError):
        catalog.add_alias(db, owner.id, p, " - ")


def test_manual_alias_shared_between_products_warns(db, owner):
    a = make_product(db, owner.id, "ปูนเสือ 50", "ถุง", "145", aliases=["ปูน"])
    b = make_product(db, owner.id, "ปูนเสือ 40", "ถุง", "125")
    _, warning = catalog.add_alias(db, owner.id, b, "ปูน")
    assert warning and a.name in warning


def test_delete_alias(db, owner):
    p = make_product(db, owner.id, "ปูนซีเมนต์ตราเสือ", "ถุง", "145", aliases=["ปูนเสือ"])
    catalog.delete_alias(db, owner.id, p.aliases[0])
    db.commit()
    db.refresh(p)
    assert p.aliases == []
    assert "alias_delete" in audit_actions(db, p.id)
