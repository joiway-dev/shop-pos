from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import User
from app.routes.deps import DB, CurrentUser
from app.services import sales
from app.services.clock import to_local
from app.services.documents import DEFAULT_FORMAT, TITLES
from app.services.thai_text import baht_text
from app.templating import templates

router = APIRouter(prefix="/sales")
PAGE_SIZE = 50


def _get(db, sale_id: int):
    sale = sales.get_sale(db, sale_id)
    if sale is None:
        raise HTTPException(status_code=404)
    return sale


@router.get("")
def sales_list(
    request: Request, db: DB, user: CurrentUser, q: str = "", day: str = "", status: str = "", page: int = 1
):
    try:
        day_value = date.fromisoformat(day) if day else None
    except ValueError:
        day_value = None
    page = max(1, page)
    rows, total = sales.list_sales(db, q, day_value, status, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(request, "sales/list.html", {
        "user": user, "rows": rows, "total": total, "q": q, "day": day, "status": status,
        "page": page, "pages": max(1, -(-total // PAGE_SIZE)), "titles": TITLES, "to_local": to_local,
    })


@router.get("/{sale_id}")
def sale_detail(request: Request, db: DB, user: CurrentUser, sale_id: int, error: str | None = None,
                done: str | None = None):
    sale = _get(db, sale_id)
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_([sale.user_id, sale.voided_by or 0]))}
    return templates.TemplateResponse(request, "sales/detail.html", {
        "user": user, "sale": sale, "title": TITLES[sale.doc_type], "to_local": to_local, "names": names,
        "default_format": DEFAULT_FORMAT.get(sale.doc_type, "80mm"), "error": error, "done": done,
    })


@router.post("/{sale_id}/void")
def sale_void(
    request: Request,
    db: DB,
    user: CurrentUser,
    sale_id: int,
    reason: Annotated[str, Form()] = "",
    owner_pin: Annotated[str, Form()] = "",
):
    sale = _get(db, sale_id)
    try:
        sales.void_sale(db, user, sale, reason, owner_pin)
        db.commit()
    except sales.SaleError as e:
        if e.pin_failed:
            db.commit()  # keep the failed-PIN counter
        else:
            db.rollback()
        sale = _get(db, sale_id)
        names = {u.id: u.name for u in db.query(User).filter(User.id.in_([sale.user_id]))}
        return templates.TemplateResponse(request, "sales/detail.html", {
            "user": user, "sale": sale, "title": TITLES[sale.doc_type], "to_local": to_local, "names": names,
            "default_format": DEFAULT_FORMAT.get(sale.doc_type, "80mm"), "error": str(e), "reason": reason,
        }, status_code=400)
    return RedirectResponse(f"/sales/{sale_id}?done=voided", status_code=303)


@router.get("/{sale_id}/print")
def sale_print(request: Request, db: DB, user: CurrentUser, sale_id: int, format: str = "", auto: int = 1):
    sale = _get(db, sale_id)
    fmt = format if format in ("80mm", "a4") else DEFAULT_FORMAT.get(sale.doc_type, "80mm")
    is_copy = sales.record_print(db, user, sale, fmt)
    db.commit()
    cashier = db.get(User, sale.user_id)
    template = "print/receipt_80mm.html" if fmt == "80mm" else "print/a4.html"
    return templates.TemplateResponse(request, template, {
        "sale": sale, "shop": sale.shop_snapshot, "buyer": sale.buyer_snapshot, "title": TITLES[sale.doc_type],
        "is_copy": is_copy, "voided": sale.status == "voided", "issued_at": to_local(sale.created_at),
        "cashier": cashier.name if cashier else "", "baht_text": baht_text(sale.total),
        "is_vat": sale.shop_snapshot.get("vat_mode") == "vat", "vat_rate": sale.vat_rate_snapshot / 100,
        "auto_print": bool(auto),
    })
