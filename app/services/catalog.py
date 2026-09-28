"""Categories, products, units and aliases."""

import re
from dataclasses import dataclass, field

from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Category, Product, ProductAlias, ProductUnit
from app.models.catalog import ALIAS_SOURCE_MANUAL, ALIAS_SOURCE_LEARNED, QTY_SCALE
from app.services.audit import log_action
from app.services.matching.matcher import mark_catalog_changed
from app.services.matching.normalize import canonical_unit, normalize
from app.services.money import AmountError, format_money, parse_money, parse_qty

MAX_EXTRA_UNITS = 10
_SKU_AUTO_RE = re.compile(r"^P(\d{5,})$")


class CatalogError(ValueError):
    """`errors` maps field -> Thai message ("units" -> list of messages)."""

    def __init__(self, errors: dict | str):
        if isinstance(errors, str):
            errors = {"_": errors}
        self.errors = errors
        super().__init__("; ".join(str(v) for v in errors.values()))


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


# --- categories ----------------------------------------------------------------


def list_categories(db: Session, include_inactive: bool = True) -> list[Category]:
    stmt = select(Category).order_by(Category.is_active.desc(), Category.name)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    return list(db.scalars(stmt))


def _check_category_name(db: Session, name: str, exclude_id: int | None = None) -> str:
    name = _clean(name)
    if not name:
        raise CatalogError({"name": "กรุณากรอกชื่อหมวดหมู่"})
    if len(name) > 100:
        raise CatalogError({"name": "ชื่อหมวดหมู่ยาวเกิน 100 ตัวอักษร"})
    stmt = select(Category.id).where(func.lower(Category.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Category.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise CatalogError({"name": "มีหมวดหมู่ชื่อนี้แล้ว"})
    return name


def create_category(db: Session, actor_id: int, name: str) -> Category:
    category = Category(name=_check_category_name(db, name), is_active=True)
    db.add(category)
    db.flush()
    log_action(db, actor_id, "category_create", "category", category.id, {"name": category.name})
    return category


def rename_category(db: Session, actor_id: int, category: Category, name: str) -> None:
    name = _check_category_name(db, name, exclude_id=category.id)
    if name != category.name:
        log_action(db, actor_id, "category_update", "category", category.id, {"name": [category.name, name]})
        category.name = name


def set_category_active(db: Session, actor_id: int, category: Category, active: bool) -> None:
    if category.is_active != active:
        category.is_active = active
        action = "category_activate" if active else "category_deactivate"
        log_action(db, actor_id, action, "category", category.id, {"name": category.name})


def get_or_create_category(db: Session, actor_id: int, name: str) -> Category:
    name = _clean(name)
    existing = db.scalar(select(Category).where(func.lower(Category.name) == name.lower()))
    return existing or create_category(db, actor_id, name)


# --- products ------------------------------------------------------------------


@dataclass
class UnitInput:
    unit_name: str
    factor: str  # how many base units, e.g. "40"
    price: str
    barcode: str = ""


@dataclass
class ProductInput:
    name: str
    base_unit: str
    base_price: str
    sku: str = ""
    barcode: str = ""
    category_id: int | None = None
    vat_exempt: bool = False
    min_stock: str = "0"
    default_unit: str = ""  # name of the default sale unit; "" = base unit
    units: list[UnitInput] = field(default_factory=list)  # extra (non-base) units


@dataclass
class _ParsedUnit:
    unit_name: str
    factor: int
    price: int
    barcode: str | None


def next_auto_sku(db: Session) -> str:
    numbers = [
        int(m.group(1))
        for sku in db.scalars(select(Product.sku).where(Product.sku.like("P%")))
        if (m := _SKU_AUTO_RE.match(sku))
    ]
    return f"P{max(numbers, default=0) + 1:05d}"


def _barcode_owner(db: Session, barcode: str, exclude_product_id: int | None) -> str | None:
    """Name of another product already using this barcode (product or unit)."""
    stmt = select(Product.name).where(Product.barcode == barcode)
    unit_stmt = (
        select(Product.name)
        .join(ProductUnit, ProductUnit.product_id == Product.id)
        .where(ProductUnit.barcode == barcode)
    )
    if exclude_product_id:
        stmt = stmt.where(Product.id != exclude_product_id)
        unit_stmt = unit_stmt.where(Product.id != exclude_product_id)
    return db.scalar(stmt) or db.scalar(unit_stmt)


def _validate_product(db: Session, data: ProductInput, product: Product | None):
    errors: dict = {}
    exclude_id = product.id if product else None

    name = _clean(data.name)
    if not name:
        errors["name"] = "กรุณากรอกชื่อสินค้า"
    elif len(name) > 200:
        errors["name"] = "ชื่อสินค้ายาวเกิน 200 ตัวอักษร"
    elif not normalize(name):
        errors["name"] = "ชื่อสินค้าต้องมีตัวอักษรหรือตัวเลข"

    sku = _clean(data.sku)
    if not sku:
        sku = product.sku if product else next_auto_sku(db)
    elif len(sku) > 50:
        errors["sku"] = "รหัสสินค้ายาวเกิน 50 ตัวอักษร"
    else:
        stmt = select(Product.id).where(Product.sku == sku)
        if exclude_id:
            stmt = stmt.where(Product.id != exclude_id)
        if db.scalar(stmt) is not None:
            errors["sku"] = "รหัสสินค้านี้มีอยู่แล้ว"

    category_id = data.category_id or None
    if category_id and db.get(Category, category_id) is None:
        errors["category_id"] = "ไม่พบหมวดหมู่"

    base_unit = _clean(data.base_unit)
    if not base_unit:
        errors["base_unit"] = "กรุณากรอกหน่วยฐาน เช่น ถุง, เส้น"
    elif len(base_unit) > 30:
        errors["base_unit"] = "ชื่อหน่วยยาวเกิน 30 ตัวอักษร"

    try:
        base_price = parse_money(data.base_price, "ราคาหน่วยฐาน")
    except AmountError as e:
        errors["base_price"] = str(e)
        base_price = 0

    try:
        min_stock = parse_qty(data.min_stock or "0", "สต็อกขั้นต่ำ", allow_zero=True)
    except AmountError as e:
        errors["min_stock"] = str(e)
        min_stock = 0

    # Units (base + extras). Names are compared in canonical form ("เมตร" == "ม.").
    unit_errors: list[str] = []
    seen_units = {canonical_unit(base_unit)} if base_unit else set()
    units: list[_ParsedUnit] = []
    extras = [u for u in data.units if _clean(u.unit_name) or u.factor.strip() or u.price.strip()]
    if len(extras) > MAX_EXTRA_UNITS:
        unit_errors.append(f"มีหน่วยเพิ่มได้ไม่เกิน {MAX_EXTRA_UNITS} หน่วย")
    for row_no, u in enumerate(extras, start=1):
        label = f"หน่วยเพิ่มแถวที่ {row_no}"
        unit_name = _clean(u.unit_name)
        if not unit_name:
            unit_errors.append(f"{label}: กรุณากรอกชื่อหน่วย")
            continue
        if len(unit_name) > 30:
            unit_errors.append(f"{label}: ชื่อหน่วยยาวเกิน 30 ตัวอักษร")
            continue
        if canonical_unit(unit_name) in seen_units:
            unit_errors.append(f"{label}: ชื่อหน่วย \"{unit_name}\" ซ้ำ")
            continue
        seen_units.add(canonical_unit(unit_name))
        try:
            factor = parse_qty(u.factor, "จำนวนหน่วยฐาน")
            price = parse_money(u.price, "ราคา")
        except AmountError as e:
            unit_errors.append(f"{label}: {e}")
            continue
        units.append(_ParsedUnit(unit_name, factor, price, _clean(u.barcode) or None))

    default_unit = _clean(data.default_unit) or base_unit
    if default_unit != base_unit and default_unit not in {u.unit_name for u in units}:
        unit_errors.append("หน่วยขายหลักต้องเป็นหน่วยฐานหรือหน่วยที่เพิ่มไว้")

    # Barcodes: unique within the product and across the whole catalog.
    barcode = _clean(data.barcode) or None
    all_barcodes = [b for b in [barcode] + [u.barcode for u in units] if b]
    for b in all_barcodes:
        if len(b) > 50:
            errors["barcode"] = "บาร์โค้ดยาวเกิน 50 ตัวอักษร"
        elif all_barcodes.count(b) > 1:
            errors["barcode"] = f"บาร์โค้ด {b} ใส่ซ้ำกันในสินค้านี้"
        elif owner := _barcode_owner(db, b, exclude_id):
            errors["barcode"] = f"บาร์โค้ด {b} ใช้กับสินค้า \"{owner}\" อยู่แล้ว"

    if unit_errors:
        errors["units"] = unit_errors
    if errors:
        raise CatalogError(errors)

    values = {
        "sku": sku,
        "barcode": barcode,
        "name": name,
        "name_normalized": normalize(name),
        "category_id": category_id,
        "base_unit": base_unit,
        "vat_exempt": bool(data.vat_exempt),
        "min_stock_qty": min_stock,
    }
    base = _ParsedUnit(base_unit, QTY_SCALE, base_price, None)
    return values, base, units, default_unit


def create_product(db: Session, actor_id: int, data: ProductInput) -> Product:
    values, base, units, default_unit = _validate_product(db, data, None)
    product = Product(**values, avg_cost=0, is_active=True)
    for u in [base, *units]:
        product.units.append(
            ProductUnit(
                unit_name=u.unit_name, factor_to_base=u.factor, price=u.price,
                barcode=u.barcode, is_default_sale_unit=(u.unit_name == default_unit),
            )
        )
    db.add(product)
    db.flush()
    log_action(
        db, actor_id, "product_create", "product", product.id,
        {"sku": product.sku, "name": product.name,
         "prices": {u.unit_name: format_money(u.price) for u in product.units}},
    )
    mark_catalog_changed(db)
    return product


def update_product(db: Session, actor_id: int, product: Product, data: ProductInput) -> Product:
    values, base, units, default_unit = _validate_product(db, data, product)

    changes = {}
    for key, new in values.items():
        old = getattr(product, key)
        if old != new:
            if key != "name_normalized":
                changes[key] = [old, new]
            setattr(product, key, new)

    # Pair each wanted unit with an existing row (by name; the base row may be renamed).
    old_base = product.base_unit_row
    existing = {u.unit_name: u for u in product.units if u is not old_base}
    pairs: list[tuple[_ParsedUnit, ProductUnit | None]] = [(base, old_base)]
    pairs += [(u, existing.get(u.unit_name)) for u in units]
    kept = {id(row) for _, row in pairs if row is not None}

    removed = [u.unit_name for u in product.units if id(u) not in kept]
    if removed:
        changes["units_removed"] = removed
        # Delete first so a removed unit's name can be reused without tripping
        # the (product_id, unit_name) unique constraint.
        product.units = [u for u in product.units if id(u) in kept]
        db.flush()

    price_changes = []
    for u, row in pairs:
        if row is None:
            row = ProductUnit(unit_name=u.unit_name, factor_to_base=u.factor, price=u.price)
            product.units.append(row)
            changes.setdefault("units_added", []).append(u.unit_name)
        else:
            if row.price != u.price:
                price_changes.append(
                    {"unit": u.unit_name, "old": format_money(row.price), "new": format_money(u.price)}
                )
            if row.factor_to_base != u.factor:
                changes.setdefault("unit_factor", []).append(
                    {"unit": u.unit_name, "old": row.factor_to_base, "new": u.factor}
                )
        row.unit_name = u.unit_name
        row.factor_to_base = u.factor
        row.price = u.price
        row.barcode = u.barcode
        row.is_default_sale_unit = u.unit_name == default_unit

    if changes:
        log_action(db, actor_id, "product_update", "product", product.id, {"changes": changes})
    if price_changes:
        log_action(db, actor_id, "price_change", "product", product.id,
                   {"name": product.name, "prices": price_changes})
    db.flush()
    mark_catalog_changed(db)
    return product


def set_product_active(db: Session, actor_id: int, product: Product, active: bool) -> None:
    if product.is_active != active:
        product.is_active = active
        action = "product_activate" if active else "product_deactivate"
        log_action(db, actor_id, action, "product", product.id, {"name": product.name})
        mark_catalog_changed(db)


def get_product(db: Session, product_id: int) -> Product:
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.units), selectinload(Product.aliases), selectinload(Product.category))
    )
    if product is None:
        raise CatalogError("ไม่พบสินค้า")
    return product


def search_products(
    db: Session,
    q: str = "",
    category_id: int | None = None,
    include_inactive: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Product], int]:
    stmt = select(Product)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    q = q.strip()
    if q:
        nq = normalize(q)
        alias_hit = select(ProductAlias.product_id).where(ProductAlias.alias_normalized.contains(nq))
        unit_hit = select(ProductUnit.product_id).where(ProductUnit.barcode == q)
        stmt = stmt.where(
            or_(
                Product.name_normalized.contains(nq) if nq else false(),
                Product.id.in_(alias_hit) if nq else false(),
                Product.sku == q,
                Product.barcode == q,
                Product.id.in_(unit_hit),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.options(selectinload(Product.units), selectinload(Product.category))
        .order_by(Product.name)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


# --- aliases -------------------------------------------------------------------


def alias_owners(db: Session, alias_normalized: str, exclude_product_id: int | None = None) -> list[Product]:
    stmt = (
        select(Product)
        .join(ProductAlias, ProductAlias.product_id == Product.id)
        .where(ProductAlias.alias_normalized == alias_normalized)
    )
    if exclude_product_id:
        stmt = stmt.where(Product.id != exclude_product_id)
    return list(db.scalars(stmt))


def add_alias(
    db: Session, actor_id: int, product: Product, text: str, source: str = ALIAS_SOURCE_MANUAL
) -> tuple[ProductAlias, str | None]:
    """Add an alias. Returns (alias, warning). Manual aliases may be shared
    between products (with a warning); callers handle learned conflicts."""
    alias_text = _clean(text)
    alias_normalized = normalize(alias_text)
    if not alias_normalized:
        raise CatalogError({"alias": "กรุณากรอกชื่อเรียก"})
    if len(alias_text) > 200:
        raise CatalogError({"alias": "ชื่อเรียกยาวเกิน 200 ตัวอักษร"})
    if alias_normalized == product.name_normalized:
        raise CatalogError({"alias": "ชื่อเรียกนี้เหมือนชื่อสินค้าอยู่แล้ว ไม่ต้องเพิ่ม"})
    if db.scalar(
        select(ProductAlias.id).where(
            ProductAlias.product_id == product.id, ProductAlias.alias_normalized == alias_normalized
        )
    ):
        raise CatalogError({"alias": "สินค้านี้มีชื่อเรียกนี้แล้ว"})

    others = alias_owners(db, alias_normalized, exclude_product_id=product.id)
    warning = None
    if others:
        names = ", ".join(p.name for p in others)
        warning = f"คำว่า \"{alias_text}\" เป็นชื่อเรียกของ {names} ด้วย — ตอนขายจะต้องเลือกสินค้าเอง"

    alias = ProductAlias(alias_text=alias_text, alias_normalized=alias_normalized, source=source, hit_count=0)
    product.aliases.append(alias)  # keeps an already-loaded collection current
    db.flush()
    action = "alias_learn" if source == ALIAS_SOURCE_LEARNED else "alias_add"
    log_action(db, actor_id, action, "product", product.id, {"alias": alias_text, "product": product.name})
    mark_catalog_changed(db)
    return alias, warning


def delete_alias(db: Session, actor_id: int, alias: ProductAlias) -> None:
    log_action(
        db, actor_id, "alias_delete", "product", alias.product_id,
        {"alias": alias.alias_text, "source": alias.source},
    )
    alias.product.aliases.remove(alias)  # delete-orphan removes the row
    db.flush()
    mark_catalog_changed(db)


def list_aliases(db: Session, source: str | None = None, q: str = "") -> list[ProductAlias]:
    stmt = (
        select(ProductAlias)
        .join(Product)
        .options(selectinload(ProductAlias.product))
        .order_by(ProductAlias.created_at.desc())
    )
    if source:
        stmt = stmt.where(ProductAlias.source == source)
    if q.strip():
        nq = normalize(q)
        stmt = stmt.where(or_(ProductAlias.alias_normalized.contains(nq), Product.name_normalized.contains(nq)))
    return list(db.scalars(stmt.limit(500)))
