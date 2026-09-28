from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import Customer
from app.routes.deps import DB, CurrentUser
from app.services import customers, receivables
from app.services.clock import now_local, to_local
from app.services.customers import CustomerError, CustomerInput
from app.services.money import format_money_input
from app.services.receivables import AGING_BUCKETS
from app.services.settings import get_shop_settings, shop_snapshot
from app.templating import templates

router = APIRouter(prefix="/customers")


def _get(db, customer_id: int) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None:
        raise HTTPException(status_code=404)
    return c


@router.get("")
def customer_list(request: Request, db: DB, user: CurrentUser, q: str = "", show_inactive: bool = False,
                  done: str | None = None):
    rows = customers.search_customers(db, q, include_inactive=show_inactive and user.is_owner, limit=200)
    balances: dict[int, int] = {}
    for bill in receivables.open_bills(db):
        balances[bill.sale.customer_id] = balances.get(bill.sale.customer_id, 0) + bill.outstanding
    return templates.TemplateResponse(request, "customers/list.html", {
        "user": user, "rows": rows, "q": q, "show_inactive": show_inactive, "balances": balances, "done": done,
        "form": {"name": "", "phone": "", "address": "", "tax_id": "", "branch": "", "note": "",
                 "credit_limit": "0", "credit_days": "0"},
    })


def _form(name, phone, address, tax_id, branch, note, credit_limit, credit_days) -> dict:
    return {"name": name, "phone": phone, "address": address, "tax_id": tax_id, "branch": branch, "note": note,
            "credit_limit": credit_limit, "credit_days": credit_days}


@router.post("")
def customer_create(
    request: Request, db: DB, user: CurrentUser,
    name: Annotated[str, Form()] = "", phone: Annotated[str, Form()] = "", address: Annotated[str, Form()] = "",
    tax_id: Annotated[str, Form()] = "", branch: Annotated[str, Form()] = "", note: Annotated[str, Form()] = "",
    credit_limit: Annotated[str, Form()] = "0", credit_days: Annotated[str, Form()] = "0",
):
    data = CustomerInput(name, phone, address, tax_id, branch, note,
                         credit_limit if user.is_owner else None, credit_days if user.is_owner else None)
    try:
        c = customers.save_customer(db, user, None, data)
        db.commit()
    except CustomerError as e:
        db.rollback()
        return templates.TemplateResponse(request, "customers/list.html", {
            "user": user, "rows": customers.search_customers(db, limit=200), "q": "", "show_inactive": False,
            "balances": {}, "error": str(e),
            "form": _form(name, phone, address, tax_id, branch, note, credit_limit, credit_days),
        }, status_code=400)
    return RedirectResponse(f"/customers/{c.id}?done=created", status_code=303)


def _render_detail(request, db, user, c, error=None, done=None, form=None, status_code=200):
    bills = receivables.open_bills(db, c.id)
    return templates.TemplateResponse(request, "customers/detail.html", {
        "user": user, "c": c, "bills": bills, "balance": sum(b.outstanding for b in bills),
        "payments": receivables.payments_of(db, c.id), "to_local": to_local, "error": error, "done": done,
        "today": now_local().date(),
        "form": form or _form(c.name, c.phone, c.address, c.tax_id or "", c.branch or "", c.note,
                              format_money_input(c.credit_limit), str(c.credit_days)),
    }, status_code=status_code)


@router.get("/{customer_id}")
def customer_detail(request: Request, db: DB, user: CurrentUser, customer_id: int, done: str | None = None):
    return _render_detail(request, db, user, _get(db, customer_id), done=done)


@router.post("/{customer_id}")
def customer_update(
    request: Request, db: DB, user: CurrentUser, customer_id: int,
    name: Annotated[str, Form()] = "", phone: Annotated[str, Form()] = "", address: Annotated[str, Form()] = "",
    tax_id: Annotated[str, Form()] = "", branch: Annotated[str, Form()] = "", note: Annotated[str, Form()] = "",
    credit_limit: Annotated[str, Form()] = "0", credit_days: Annotated[str, Form()] = "0",
):
    c = _get(db, customer_id)
    data = CustomerInput(name, phone, address, tax_id, branch, note,
                         credit_limit if user.is_owner else None, credit_days if user.is_owner else None)
    try:
        customers.save_customer(db, user, c, data)
        db.commit()
    except CustomerError as e:
        db.rollback()
        return _render_detail(request, db, user, _get(db, customer_id), error=str(e),
                              form=_form(name, phone, address, tax_id, branch, note, credit_limit, credit_days),
                              status_code=400)
    return RedirectResponse(f"/customers/{customer_id}?done=saved", status_code=303)


@router.post("/{customer_id}/active")
def customer_active(request: Request, db: DB, user: CurrentUser, customer_id: int, active: Annotated[bool, Form()]):
    c = _get(db, customer_id)
    try:
        customers.set_customer_active(db, user, c, active)
        db.commit()
    except CustomerError as e:
        db.rollback()
        return _render_detail(request, db, user, c, error=str(e), status_code=403)
    return RedirectResponse(f"/customers/{customer_id}?done=saved", status_code=303)


@router.get("/{customer_id}/statement")
def customer_statement(request: Request, db: DB, user: CurrentUser, customer_id: int, auto: int = 1):
    """ใบแจ้งยอด: open bills with aging, printable A4."""
    c = _get(db, customer_id)
    bills = receivables.open_bills(db, c.id)
    buckets = [0, 0, 0, 0]
    for b in bills:
        for i, (_, low, high) in enumerate(AGING_BUCKETS):
            if b.age_days >= low and (high is None or b.age_days <= high):
                buckets[i] += b.outstanding
                break
    return templates.TemplateResponse(request, "print/statement_a4.html", {
        "c": c, "bills": bills, "total": sum(b.outstanding for b in bills), "buckets": list(zip(AGING_BUCKETS, buckets)),
        "shop": shop_snapshot(get_shop_settings(db)), "today": now_local(), "to_local": to_local,
        "auto_print": bool(auto),
    })
