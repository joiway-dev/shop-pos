from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import ProductAlias
from app.routes.deps import DB, OwnerUser
from app.services import catalog
from app.templating import templates

router = APIRouter(prefix="/aliases")


@router.get("")
def aliases_page(request: Request, db: DB, user: OwnerUser, source: str = "", q: str = "", done: str | None = None):
    source = source if source in ("manual", "learned") else ""
    return templates.TemplateResponse(
        request,
        "backoffice/aliases.html",
        {
            "user": user,
            "aliases": catalog.list_aliases(db, source or None, q),
            "source": source,
            "q": q,
            "done": done,
        },
    )


@router.post("/{alias_id}/delete")
def alias_delete(db: DB, user: OwnerUser, alias_id: int, back: str = ""):
    alias = db.get(ProductAlias, alias_id)
    if alias is None:
        raise HTTPException(status_code=404)
    product_id = alias.product_id
    catalog.delete_alias(db, user.id, alias)
    db.commit()
    if back == "product":
        return RedirectResponse(f"/products/{product_id}#aliases", status_code=303)
    return RedirectResponse("/aliases?done=1", status_code=303)
