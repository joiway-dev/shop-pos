"""Receivables pages: aging summary, receive payment (RV), payment detail/void/print."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import Customer, Sale, User
from app.models.customers import PAY_METHODS
from app.routes.deps import DB, CurrentUser
from app.services import receivables
from app.services.clock import to_local
from app.services.receivables import AGING_BUCKETS, ReceivableError
from app.services.thai_text import baht_text
from app.templating import templates

router = APIRouter(prefix="/ar")


@router.get("")
def ar_page(request: Request, db: DB, user: CurrentUser):
    rows = receivables.aging(db)
    totals = [sum(r.buckets[i] for r in rows) for i in range(4)]
    return templates.TemplateResponse(request, "customers/ar.html", {
        "user": user, "rows": rows, "labels": [b[0] for b in AGING_BUCKETS], "totals": totals,
        "grand": sum(totals), "overdue": sum(r.overdue for r in rows),
    })


def _customer(db, customer_id: int) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None:
        raise HTTPException(status_code=404)
    return c


def _render_pay(request, db, user, c, error=None, form=None, status_code=200):
    return templates.TemplateResponse(request, "customers/pay.html", {
        "user": user, "c": c, "bills": receivables.open_bills(db, c.id), "methods": PAY_METHODS,
        "error": error, "form": form or {"amount": "", "method": "cash", "note": "", "auto": True, "alloc": {}},
        "to_local": to_local,
    }, status_code=status_code)


@router.get("/pay/{customer_id}")
def pay_page(request: Request, db: DB, user: CurrentUser, customer_id: int):
    return _render_pay(request, db, user, _customer(db, customer_id))


@router.post("/pay/{customer_id}")
async def pay_submit(request: Request, db: DB, user: CurrentUser, customer_id: int):
    c = _customer(db, customer_id)
    form = await request.form()
    auto = form.get("auto") == "true"
    alloc = {int(k[6:]): str(v) for k, v in form.items() if k.startswith("alloc_") and k[6:].isdigit()}
    try:
        payment = receivables.receive_payment(
            db, user, c, str(form.get("amount", "")), str(form.get("method", "cash")), str(form.get("note", "")),
            allocations=None if auto else alloc,
        )
        db.commit()
    except ReceivableError as e:
        db.rollback()
        return _render_pay(request, db, user, _customer(db, customer_id), error=str(e), status_code=400, form={
            "amount": form.get("amount", ""), "method": form.get("method", "cash"), "note": form.get("note", ""),
            "auto": auto, "alloc": {k: v for k, v in alloc.items()},
        })
    return RedirectResponse(f"/ar/payments/{payment.id}?done=created", status_code=303)


def _payment(db, payment_id: int):
    p = receivables.get_payment(db, payment_id)
    if p is None:
        raise HTTPException(status_code=404)
    return p


def _render_payment(request, db, user, p, error=None, done=None, status_code=200):
    sales = {s.id: s for s in db.query(Sale).filter(Sale.id.in_([a.sale_id for a in p.allocations]))}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_([p.user_id, p.voided_by or 0]))}
    return templates.TemplateResponse(request, "customers/payment.html", {
        "user": user, "p": p, "sales": sales, "names": names, "methods": PAY_METHODS, "to_local": to_local,
        "error": error, "done": done,
    }, status_code=status_code)


@router.get("/payments/{payment_id}")
def payment_detail(request: Request, db: DB, user: CurrentUser, payment_id: int, done: str | None = None):
    return _render_payment(request, db, user, _payment(db, payment_id), done=done)


@router.post("/payments/{payment_id}/void")
async def payment_void(request: Request, db: DB, user: CurrentUser, payment_id: int):
    form = await request.form()
    p = _payment(db, payment_id)
    try:
        receivables.void_payment(db, user, p, str(form.get("reason", "")), str(form.get("owner_pin", "")))
        db.commit()
    except ReceivableError as e:
        if e.pin_failed:
            db.commit()  # keep the failed-PIN counter
        else:
            db.rollback()
        return _render_payment(request, db, user, _payment(db, payment_id), error=str(e), status_code=400)
    return RedirectResponse(f"/ar/payments/{payment_id}?done=voided", status_code=303)


@router.get("/payments/{payment_id}/print")
def payment_print(request: Request, db: DB, user: CurrentUser, payment_id: int, auto: int = 1):
    p = _payment(db, payment_id)
    is_copy = receivables.record_print(db, user, p)
    db.commit()
    sales = {s.id: s for s in db.query(Sale).filter(Sale.id.in_([a.sale_id for a in p.allocations]))}
    cashier = db.get(User, p.user_id)
    return templates.TemplateResponse(request, "print/payment_a4.html", {
        "p": p, "shop": p.shop_snapshot, "cust": p.customer_snapshot, "sales": sales, "is_copy": is_copy,
        "voided": p.status == "voided", "issued_at": to_local(p.created_at), "methods": PAY_METHODS,
        "baht_text": baht_text(p.amount), "cashier": cashier.name if cashier else "", "auto_print": bool(auto),
        "to_local": to_local,
    })
