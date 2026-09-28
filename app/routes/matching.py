"""Matching test page (SPEC phase 1): single query + multi-line paste."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from app.routes.deps import DB, CurrentUser
from app.services import catalog
from app.services.catalog import CatalogError
from app.services.matching import learning, matcher, order_parser
from app.templating import templates

router = APIRouter(prefix="/matching")


@router.get("")
def matching_page(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request, "backoffice/matching.html", {"user": user})


@router.post("/search")
def matching_search(request: Request, db: DB, user: CurrentUser, query: Annotated[str, Form()] = ""):
    result = matcher.match(db, query) if query.strip() else None
    return templates.TemplateResponse(
        request, "backoffice/partials/match_result.html", {"user": user, "result": result}
    )


@router.post("/parse")
def matching_parse(request: Request, db: DB, user: CurrentUser, text: Annotated[str, Form()] = ""):
    lines = order_parser.parse_order(db, text)
    return templates.TemplateResponse(
        request,
        "backoffice/partials/order_lines.html",
        {"user": user, "lines": lines, "ready": order_parser.ready_to_bill(lines)},
    )


@router.post("/learn")
def matching_learn(
    request: Request,
    db: DB,
    user: CurrentUser,
    query: Annotated[str, Form()],
    product_id: Annotated[int, Form()],
    remember: Annotated[bool, Form()] = False,
):
    """Staff picked a product for `query`; optionally remember it (SPEC 4.3)."""
    try:
        product = catalog.get_product(db, product_id)
    except CatalogError:
        raise HTTPException(status_code=404) from None
    result = None
    if remember:
        result = learning.learn_alias(db, user.id, query, product)
        db.commit()
    return templates.TemplateResponse(
        request,
        "backoffice/partials/learn_result.html",
        {"user": user, "product": product, "result": result, "query": query},
    )
