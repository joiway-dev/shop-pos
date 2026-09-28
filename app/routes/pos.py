"""POS page and its JSON endpoints (thin: all rules live in services)."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Category, Product
from app.models.settings import VAT_MODE_VAT
from app.routes.deps import DB, CurrentUser
from app.services import catalog, sales, stock
from app.services.documents import DEFAULT_FORMAT, TITLES
from app.services.matching import learning, matcher, order_parser
from app.services.matching.normalize import canonical_unit
from app.services.money import format_money, format_qty, format_qty_input
from app.services.settings import get_shop_settings
from app.templating import templates

router = APIRouter(prefix="/pos")
GRID_LIMIT = 60


# --- page + product grid ---------------------------------------------------------


def _grid_products(db, category_id: int | None, q: str):
    if q.strip():
        products, _ = catalog.search_products(db, q, category_id, limit=GRID_LIMIT)
    else:
        stmt = select(Product).where(Product.is_active.is_(True)).options(selectinload(Product.units))
        if category_id:
            stmt = stmt.where(Product.category_id == category_id)
        products = list(db.scalars(stmt.order_by(Product.name).limit(GRID_LIMIT)))
    on_hand = stock.balances(db, [p.id for p in products])
    return [(p, p.default_unit, on_hand.get(p.id, 0)) for p in products]


@router.get("")
def pos_page(request: Request, db: DB, user: CurrentUser, reissue: int | None = None):
    settings = get_shop_settings(db)
    categories = [c for c in catalog.list_categories(db, include_inactive=False)]
    return templates.TemplateResponse(request, "pos/pos.html", {
        "user": user,
        "categories": categories,
        "grid": _grid_products(db, None, ""),
        "config": {
            "isOwner": user.is_owner,
            "vat": settings.vat_mode == VAT_MODE_VAT,
            "buyerRequired": settings.vat_mode == VAT_MODE_VAT and not settings.allow_abbreviated_invoice,
            "reissue": reissue,
        },
    })


@router.get("/products")
def pos_products(request: Request, db: DB, user: CurrentUser, category_id: str = "", q: str = ""):
    cat = int(category_id) if category_id.isdigit() else None
    return templates.TemplateResponse(
        request, "pos/partials/grid.html", {"user": user, "grid": _grid_products(db, cat, q)}
    )


# --- search / parse / learn -----------------------------------------------------


def _product_options(db, product_ids: list[int], unit_text: str | None = None) -> dict[int, dict]:
    """Units of each product, plus which unit matches the typed unit text."""
    wanted = canonical_unit(unit_text) if unit_text else None
    out = {}
    for p in db.scalars(select(Product).where(Product.id.in_(product_ids)).options(selectinload(Product.units))):
        default = p.default_unit
        out[p.id] = {
            "units": [{"unit_id": u.id, "unit_name": u.unit_name, "price_text": format_money(u.price)} for u in p.units],
            "default_unit_id": default.id if default else None,
            "unit_match_id": next((u.id for u in p.units if wanted and canonical_unit(u.unit_name) == wanted), None),
        }
    return out


@router.get("/search")
def pos_search(db: DB, _user: CurrentUser, q: str = ""):
    result = matcher.match(db, q) if q.strip() else None
    if result is None:
        return {"status": "empty", "candidates": []}
    options = _product_options(db, [c.product_id for c in result.candidates])
    candidates = []
    for c in result.candidates:
        opt = options.get(c.product_id, {})
        unit_id = c.unit_id or opt.get("default_unit_id")
        unit = next((u for u in opt.get("units", []) if u["unit_id"] == unit_id), None)
        candidates.append({
            "product_id": c.product_id, "name": c.name, "score": c.score, "reason": c.reason_label,
            "unit_id": unit_id, "unit_name": unit["unit_name"] if unit else "",
            "price_text": unit["price_text"] if unit else "",
        })
    return {"status": result.status, "query": q, "candidates": candidates}


class ParseBody(BaseModel):
    text: str


@router.post("/parse")
def pos_parse(db: DB, _user: CurrentUser, body: ParseBody):
    lines = order_parser.parse_order(db, body.text)
    rows = []
    for l in lines:
        ids = [c.product_id for c in l.candidates] + ([l.product_id] if l.product_id else [])
        options = _product_options(db, list(set(ids)), l.unit_text)
        rows.append({
            "line_no": l.line_no, "raw": l.raw, "product_text": l.product_text, "status": l.status,
            "status_label": l.status_label, "message": l.message, "review_note": l.review_note,
            "product_id": l.product_id, "unit_id": l.unit_id, "unit_text": l.unit_text,
            "qty": format_qty_input(l.qty),
            "candidates": [{"product_id": c.product_id, "name": c.name, "score": c.score} for c in l.candidates],
            "options": {str(k): v for k, v in options.items()},
        })
    return {"lines": rows, "ready": order_parser.ready_to_bill(lines)}


class LearnBody(BaseModel):
    text: str
    product_id: int


@router.post("/learn")
def pos_learn(db: DB, user: CurrentUser, body: LearnBody):
    try:
        product = catalog.get_product(db, body.product_id)
    except catalog.CatalogError:
        raise HTTPException(status_code=404) from None
    result = learning.learn_alias(db, user.id, body.text, product)
    db.commit()
    return {"status": result.status, "message": result.message}


# --- quote / checkout -----------------------------------------------------------


class CartLine(BaseModel):
    product_id: int
    unit_id: int
    qty: str = "1"
    discount: str = ""


class QuoteBody(BaseModel):
    lines: list[CartLine] = []
    bill_discount: str = ""


class Buyer(BaseModel):
    name: str = ""
    address: str = ""
    tax_id: str = ""
    branch: str = ""


class CheckoutBody(QuoteBody):
    payment_type: str = "cash"
    cash_received: str = ""
    buyer: Buyer | None = None
    owner_pin: str = ""
    replaces_sale_id: int | None = None


def _cart(lines: list[CartLine]) -> list[sales.CartLineInput]:
    return [sales.CartLineInput(l.product_id, l.unit_id, l.qty, l.discount) for l in lines]


@router.post("/quote")
def pos_quote(db: DB, user: CurrentUser, body: QuoteBody):
    q = sales.quote(db, _cart(body.lines), body.bill_discount)
    t = q.totals
    return {
        "ok": q.ok,
        "errors": q.errors,
        "lines": [{
            "name": l.name, "sku": l.sku, "unit_id": l.unit_id, "unit_name": l.unit_name,
            "price_text": format_money(l.unit_price), "qty_text": format_qty(l.qty),
            "discount_text": format_money(l.discount) if l.discount else "",
            "line_total_text": format_money(l.line_total), "error": l.error, "stock_warning": l.stock_warning,
            "units": [{"unit_id": u.unit_id, "unit_name": u.unit_name, "price_text": format_money(u.price)} for u in l.units],
        } for l in q.lines],
        "totals": None if t is None else {
            "subtotal": t.subtotal, "subtotal_text": format_money(t.subtotal),
            "discount_text": format_money(t.discount), "vat_text": format_money(t.vat_amount),
            "vatable_text": format_money(t.vatable_amount), "exempt_text": format_money(t.exempt_amount),
            "total": t.total, "total_text": format_money(t.total),
        },
        "needs_owner_pin": q.has_discount and not user.is_owner,
    }


@router.post("/checkout")
def pos_checkout(db: DB, user: CurrentUser, body: CheckoutBody):
    data = sales.CheckoutInput(
        lines=_cart(body.lines), bill_discount=body.bill_discount, payment_type=body.payment_type,
        cash_received=body.cash_received,
        buyer=sales.BuyerInput(**body.buyer.model_dump()) if body.buyer else None,
        owner_pin=body.owner_pin, replaces_sale_id=body.replaces_sale_id,
    )
    try:
        sale = sales.create_sale(db, user, data)
        db.commit()
    except sales.SaleError as e:
        if e.pin_failed:
            db.commit()  # keep the failed-PIN counter; nothing else was written
        else:
            db.rollback()
        return {"ok": False, "error": str(e), "pin_failed": e.pin_failed}
    return {
        "ok": True, "sale_id": sale.id, "doc_no": sale.doc_no, "doc_title": TITLES[sale.doc_type],
        "total_text": format_money(sale.total),
        "change_text": format_money(sale.change_amount) if sale.change_amount is not None else None,
        "default_format": DEFAULT_FORMAT.get(sale.doc_type, "80mm"),
    }


@router.get("/reissue/{sale_id}")
def pos_reissue(db: DB, _user: CurrentUser, sale_id: int):
    """Lines of a voided sale, to load into a new cart ("ยกเลิกและออกใหม่")."""
    sale = sales.get_sale(db, sale_id)
    if sale is None:
        raise HTTPException(status_code=404)
    if sale.status != "voided" or sale.replaced_by_sale_id:
        return {"ok": False, "error": "ออกใบใหม่ได้เฉพาะบิลที่ยกเลิกแล้ว และยังไม่เคยออกใหม่"}
    units = {}
    for p in db.scalars(select(Product).where(Product.id.in_([l.product_id for l in sale.lines]))
                        .options(selectinload(Product.units))):
        units[p.id] = {u.unit_name: u.id for u in p.units}
    lines = []
    for l in sale.lines:
        unit_id = units.get(l.product_id, {}).get(l.unit_name)
        if unit_id:
            lines.append({"product_id": l.product_id, "unit_id": unit_id, "qty": format_qty_input(l.qty),
                          "discount": format_money(l.discount).replace(",", "") if l.discount else ""})
    return {"ok": True, "doc_no": sale.doc_no, "lines": lines,
            "bill_discount": format_money(sale.discount).replace(",", "") if sale.discount else ""}
