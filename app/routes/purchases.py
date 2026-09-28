from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Product, User
from app.models.settings import VAT_MODE_VAT
from app.routes.deps import DB, ReceiverUser
from app.services import purchases, suppliers
from app.services.clock import to_local
from app.services.money import format_money
from app.services.settings import get_shop_settings
from app.templating import templates

router = APIRouter(prefix="/purchases")
PAGE_SIZE = 50

STATUS_LABELS = {"pending_cost": "รอใส่ต้นทุน", "completed": "เรียบร้อย", "voided": "ยกเลิก"}
VAT_LABELS = {"none": "ไม่มี VAT", "included": "ราคารวม VAT แล้ว", "excluded": "ราคายังไม่รวม VAT"}


def _get(db, purchase_id: int):
    p = purchases.get_purchase(db, purchase_id)
    if p is None:
        raise HTTPException(status_code=404)
    return p


@router.get("")
def purchase_list(request: Request, db: DB, user: ReceiverUser, q: str = "", status: str = "", page: int = 1):
    page = max(1, page)
    rows, total = purchases.list_purchases(db, q, status, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(request, "purchasing/list.html", {
        "user": user, "rows": rows, "total": total, "q": q, "status": status, "page": page,
        "pages": max(1, -(-total // PAGE_SIZE)), "labels": STATUS_LABELS, "to_local": to_local,
    })


@router.get("/new")
def purchase_new(request: Request, db: DB, user: ReceiverUser):
    settings = get_shop_settings(db)
    return templates.TemplateResponse(request, "purchasing/new.html", {
        "user": user,
        "suppliers": suppliers.list_suppliers(db, include_inactive=False),
        "config": {"canCost": user.may_see_cost, "shopVat": settings.vat_mode == VAT_MODE_VAT},
        "vat_labels": VAT_LABELS,
    })


@router.get("/units/{product_id}")
def purchase_units(db: DB, user: ReceiverUser, product_id: int):
    p = db.scalar(select(Product).where(Product.id == product_id).options(selectinload(Product.units)))
    if p is None or not p.is_active:
        raise HTTPException(status_code=404)
    default = p.default_unit
    return {
        "product_id": p.id, "name": p.name, "base_unit": p.base_unit,
        "default_unit_id": default.id if default else None,
        "units": [{"unit_id": u.id, "unit_name": u.unit_name} for u in p.units],
        "avg_cost_text": format_money(p.avg_cost) if user.may_see_cost else None,
    }


class LineBody(BaseModel):
    product_id: int
    unit_id: int
    qty: str = "1"
    unit_cost: str = ""


class PurchaseBody(BaseModel):
    lines: list[LineBody] = []
    supplier_id: int | None = None
    supplier_invoice_no: str = ""
    supplier_invoice_date: str = ""
    vat_type: str = "none"
    note: str = ""


@router.post("")
def purchase_create(db: DB, user: ReceiverUser, body: PurchaseBody):
    data = purchases.PurchaseInput(
        lines=[purchases.PurchaseLineInput(l.product_id, l.unit_id, l.qty, l.unit_cost) for l in body.lines],
        supplier_id=body.supplier_id, supplier_invoice_no=body.supplier_invoice_no,
        supplier_invoice_date=body.supplier_invoice_date, vat_type=body.vat_type, note=body.note,
    )
    try:
        p = purchases.create_purchase(db, user, data)
        db.commit()
    except purchases.PurchaseError as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": p.id, "doc_no": p.doc_no}


def _render_detail(request, db, user, p, error=None, done=None, status_code=200):
    ids = {i for i in (p.user_id, p.costed_by, p.voided_by) if i}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids)))}
    return templates.TemplateResponse(request, "purchasing/detail.html", {
        "user": user, "p": p, "names": names, "labels": STATUS_LABELS, "vat_labels": VAT_LABELS,
        "to_local": to_local, "error": error, "done": done,
    }, status_code=status_code)


@router.get("/{purchase_id}")
def purchase_detail(request: Request, db: DB, user: ReceiverUser, purchase_id: int, done: str | None = None):
    return _render_detail(request, db, user, _get(db, purchase_id), done=done)


@router.post("/{purchase_id}/costs")
async def purchase_costs(request: Request, db: DB, user: ReceiverUser, purchase_id: int):
    form = await request.form()
    p = _get(db, purchase_id)
    costs = {l.id: str(form.get(f"cost_{l.id}", "")) for l in p.lines}
    try:
        purchases.enter_costs(db, user, p, costs, str(form.get("vat_type", "none")))
        db.commit()
    except purchases.PurchaseError as e:
        db.rollback()
        return _render_detail(request, db, user, _get(db, purchase_id), error=str(e), status_code=400)
    return RedirectResponse(f"/purchases/{purchase_id}?done=costed", status_code=303)


@router.post("/{purchase_id}/void")
async def purchase_void(request: Request, db: DB, user: ReceiverUser, purchase_id: int):
    form = await request.form()
    p = _get(db, purchase_id)
    try:
        purchases.void_purchase(db, user, p, str(form.get("reason", "")), str(form.get("owner_pin", "")))
        db.commit()
    except purchases.PurchaseError as e:
        if e.pin_failed:
            db.commit()  # keep the failed-PIN counter
        else:
            db.rollback()
        return _render_detail(request, db, user, _get(db, purchase_id), error=str(e), status_code=400)
    return RedirectResponse(f"/purchases/{purchase_id}?done=voided", status_code=303)
