from app.services import catalog
from app.services.catalog import ProductInput, UnitInput


def make_product(
    db,
    actor_id: int,
    name: str,
    base_unit: str,
    price: str,
    units: list[tuple[str, str, str] | tuple[str, str, str, str]] = (),
    aliases: list[str] = (),
    barcode: str = "",
    sku: str = "",
    default_unit: str = "",
):
    product = catalog.create_product(
        db,
        actor_id,
        ProductInput(
            name=name,
            base_unit=base_unit,
            base_price=price,
            barcode=barcode,
            sku=sku,
            default_unit=default_unit,
            units=[UnitInput(*u) for u in units],
        ),
    )
    for alias in aliases:
        catalog.add_alias(db, actor_id, product, alias)
    return product


def seed_catalog(db, actor_id: int) -> dict[str, object]:
    """A small construction-store catalog used by matcher/parser tests."""
    p = {}
    p["tiger50"] = make_product(
        db, actor_id, "ปูนซีเมนต์ตราเสือ 50 กก.", "ถุง", "145",
        units=[("พาเลท", "40", "5600", "PAL-TIGER50")], aliases=["ปูนเสือ"], barcode="8851234567890",
    )
    p["tiger40"] = make_product(db, actor_id, "ปูนซีเมนต์ตราเสือ 40 กก.", "ถุง", "125")
    p["rebar12"] = make_product(
        db, actor_id, "เหล็กเส้นกลม 12 มม.", "เส้น", "189",
        units=[("มัด", "10", "1850")], aliases=["เหล็ก 12 มิล"],
    )
    p["rebar9"] = make_product(db, actor_id, "เหล็กเส้นกลม 9 มม.", "เส้น", "105", aliases=["เหล็ก 9 มิล"])
    p["sand"] = make_product(db, actor_id, "ทรายหยาบ", "คิว", "450", aliases=["ทราย"])
    p["pipe"] = make_product(db, actor_id, "ท่อ PVC 4 นิ้ว", "เส้น", "250")
    p["wire"] = make_product(
        db, actor_id, "สายไฟ VAF 2x1.5", "เมตร", "12", units=[("ม้วน", "100", "1100")],
    )
    p["nail"] = make_product(
        db, actor_id, "ตะปู 2 นิ้ว", "กก", "60", units=[("กล่อง", "25", "1400")], default_unit="กล่อง",
    )
    db.commit()
    return p
