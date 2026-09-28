from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.models import User
from app.routes.deps import DB, CurrentUser
from app.services import quotations
from app.services.clock import now_local, to_local
from app.services.quotations import QuotationError, QuotationInput
from app.services.sales import CartLineInput
from app.services.thai_text import baht_text
from app.templating import templates

router = APIRouter(prefix="/quotations")
PAGE_SIZE = 50
STATUS_LABELS = {"open": "ใช้ได้", "expired": "หมดอายุ", "converted": "แปลงเป็นบิลแล้ว"}


def _get(db, quotation_id: int):
    qt = quotations.get_quotation(db, quotation_id)
    if qt is None:
        raise HTTPException(status_code=404)
    return qt


@router.get("")
def quotation_list(request: Request, db: DB, user: CurrentUser, q: str = "", status: str = "", page: int = 1):
    page = max(1, page)
    rows, total = quotations.list_quotations(db, q, status, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    today = now_local().date()
    return templates.TemplateResponse(request, "quotations/list.html", {
        "user": user, "rows": [(qt, quotations.status_of(qt, today)) for qt in rows], "total": total, "q": q,
        "status": status, "page": page, "pages": max(1, -(-total // PAGE_SIZE)), "labels": STATUS_LABELS,
        "to_local": to_local,
    })


class LineBody(BaseModel):
    product_id: int
    unit_id: int
    qty: str = "1"
    discount: str = ""


class QuotationBody(BaseModel):
    lines: list[LineBody] = []
    bill_discount: str = ""
    customer_id: int | None = None
    customer_name: str = ""
    valid_days: int = quotations.DEFAULT_VALID_DAYS
    note: str = ""
    owner_pin: str = ""


def _save(db, user, qt, body: QuotationBody):
    data = QuotationInput(
        lines=[CartLineInput(l.product_id, l.unit_id, l.qty, l.discount) for l in body.lines],
        bill_discount=body.bill_discount, customer_id=body.customer_id, customer_name=body.customer_name,
        valid_days=body.valid_days, note=body.note, owner_pin=body.owner_pin,
    )
    try:
        qt = quotations.save_quotation(db, user, qt, data)
        db.commit()
    except QuotationError as e:
        if e.pin_failed:
            db.commit()
        else:
            db.rollback()
        return {"ok": False, "error": str(e), "pin_failed": e.pin_failed}
    return {"ok": True, "id": qt.id, "doc_no": qt.doc_no}


@router.post("")
def quotation_create(db: DB, user: CurrentUser, body: QuotationBody):
    return _save(db, user, None, body)


@router.post("/{quotation_id}/edit")
def quotation_update(db: DB, user: CurrentUser, quotation_id: int, body: QuotationBody):
    return _save(db, user, _get(db, quotation_id), body)


@router.get("/{quotation_id}")
def quotation_detail(request: Request, db: DB, user: CurrentUser, quotation_id: int, done: str | None = None,
                     error: str | None = None):
    qt = _get(db, quotation_id)
    author = db.get(User, qt.user_id)
    return templates.TemplateResponse(request, "quotations/detail.html", {
        "user": user, "qt": qt, "state": quotations.status_of(qt), "labels": STATUS_LABELS, "to_local": to_local,
        "author": author.name if author else "-", "done": done, "error": error,
    })


@router.post("/{quotation_id}/renew")
def quotation_renew(db: DB, user: CurrentUser, quotation_id: int, days: Annotated[int, Form()] = 7):
    qt = _get(db, quotation_id)
    try:
        quotations.renew(db, user, qt, days)
        db.commit()
    except QuotationError:
        db.rollback()
        raise HTTPException(status_code=400) from None
    return RedirectResponse(f"/quotations/{quotation_id}?done=renewed", status_code=303)


@router.get("/{quotation_id}/print")
def quotation_print(request: Request, db: DB, user: CurrentUser, quotation_id: int, auto: int = 1):
    qt = _get(db, quotation_id)
    author = db.get(User, qt.user_id)
    buyer = None
    if qt.customer is not None:
        c = qt.customer
        buyer = {"name": c.name, "address": c.address, "tax_id": c.tax_id, "branch_no": c.branch, "phone": c.phone}
    elif qt.customer_name:
        buyer = {"name": qt.customer_name}
    return templates.TemplateResponse(request, "print/quotation_a4.html", {
        "qt": qt, "shop": qt.shop_snapshot, "buyer": buyer, "issued_at": to_local(qt.created_at),
        "is_vat": qt.shop_snapshot.get("vat_mode") == "vat", "vat_rate": qt.vat_rate_snapshot / 100,
        "baht_text": baht_text(qt.total), "author": author.name if author else "", "auto_print": bool(auto),
    })
