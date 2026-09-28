from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import Supplier
from app.routes.deps import DB, OwnerUser
from app.services import suppliers
from app.templating import templates

router = APIRouter(prefix="/suppliers")


def _render(request, db, user, error=None, form=None, done=None, status_code=200):
    return templates.TemplateResponse(request, "purchasing/suppliers.html", {
        "user": user, "suppliers": suppliers.list_suppliers(db), "error": error,
        "form": form or {"name": "", "phone": "", "address": "", "tax_id": ""}, "done": done,
    }, status_code=status_code)


def _get(db, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404)
    return supplier


@router.get("")
def suppliers_page(request: Request, db: DB, user: OwnerUser, done: str | None = None):
    return _render(request, db, user, done=done)


@router.post("")
def suppliers_save(
    request: Request,
    db: DB,
    user: OwnerUser,
    supplier_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    tax_id: Annotated[str, Form()] = "",
):
    supplier = _get(db, int(supplier_id)) if supplier_id.isdigit() else None
    try:
        suppliers.save_supplier(db, user.id, supplier, name, phone, address, tax_id)
        db.commit()
    except suppliers.SupplierError as e:
        db.rollback()
        form = {"name": name, "phone": phone, "address": address, "tax_id": tax_id}
        return _render(request, db, user, error=str(e), form=form, status_code=400)
    return RedirectResponse("/suppliers?done=1", status_code=303)


@router.post("/{supplier_id}/active")
def suppliers_active(db: DB, user: OwnerUser, supplier_id: int, active: Annotated[bool, Form()]):
    suppliers.set_supplier_active(db, user.id, _get(db, supplier_id), active)
    db.commit()
    return RedirectResponse("/suppliers?done=1", status_code=303)
