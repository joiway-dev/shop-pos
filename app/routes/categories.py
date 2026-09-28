from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import Category
from app.routes.deps import DB, OwnerUser
from app.services import catalog
from app.services.catalog import CatalogError
from app.templating import templates

router = APIRouter(prefix="/categories")


def _render(request, db, user, error=None, done=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "backoffice/categories.html",
        {"user": user, "categories": catalog.list_categories(db), "error": error, "done": done},
        status_code=status_code,
    )


def _get(db, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404)
    return category


@router.get("")
def categories_page(request: Request, db: DB, user: OwnerUser, done: str | None = None):
    return _render(request, db, user, done=done)


@router.post("")
def categories_create(request: Request, db: DB, user: OwnerUser, name: Annotated[str, Form()] = ""):
    try:
        catalog.create_category(db, user.id, name)
        db.commit()
    except CatalogError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return RedirectResponse("/categories?done=1", status_code=303)


@router.post("/{category_id}")
def categories_rename(
    request: Request, db: DB, user: OwnerUser, category_id: int, name: Annotated[str, Form()] = ""
):
    try:
        catalog.rename_category(db, user.id, _get(db, category_id), name)
        db.commit()
    except CatalogError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return RedirectResponse("/categories?done=1", status_code=303)


@router.post("/{category_id}/active")
def categories_set_active(db: DB, user: OwnerUser, category_id: int, active: Annotated[bool, Form()]):
    catalog.set_category_active(db, user.id, _get(db, category_id), active)
    db.commit()
    return RedirectResponse("/categories?done=1", status_code=303)
