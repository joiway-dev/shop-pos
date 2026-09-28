import re
import secrets
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.models import Purchase, PurchaseLine, Sale, User
from app.routes.deps import DB, CurrentUser, OwnerUser
from app.services import catalog, costing, opening_import, product_import, stock
from app.services.clock import to_local
from app.templating import templates

router = APIRouter(prefix="/stock")
PAGE_SIZE = 100
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

MOVE_LABELS = {
    ("sale", "sale"): "ขาย", ("sale_void", "sale"): "ยกเลิกบิลขาย",
    ("purchase", costing.REF_PURCHASE_LINE): "รับเข้า", ("purchase", costing.REF_PURCHASE_VOID): "ยกเลิกใบรับ",
    ("adjust", stock.REF_COUNT): "ปรับตามนับจริง", ("adjust", stock.REF_WRITEOFF): "ตัดออก",
    ("adjust", costing.REF_OPENING): "ยอดยกมา", ("adjust", None): "ปรับสต็อก", ("return", None): "รับคืน",
}


@router.get("")
def stock_page(request: Request, db: DB, user: CurrentUser, q: str = "", category_id: str = "",
               low: bool = False, page: int = 1):
    page = max(1, page)
    cat = int(category_id) if category_id.isdigit() else None
    rows, total = stock.stock_rows(db, q, cat, low, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(request, "stock/list.html", {
        "user": user, "rows": rows, "total": total, "q": q, "category_id": cat, "low": low, "page": page,
        "pages": max(1, -(-total // PAGE_SIZE)), "categories": catalog.list_categories(db, include_inactive=False),
        "value": stock.stock_value(db) if user.may_see_cost else None,
    })


# --- opening balances (declared before /{product_id}) ----------------------------


def _opening_path(request: Request, token: str):
    return request.app.state.config.uploads_dir / f"opening-{token}.xlsx"


def _read_opening(filename: str, content: bytes):
    return product_import.read_rows(filename, content, required=opening_import.REQUIRED)


@router.get("/opening")
def opening_page(request: Request, user: OwnerUser):
    return templates.TemplateResponse(request, "stock/opening.html", {"user": user, "report": None, "error": None})


@router.get("/opening/template.xlsx")
def opening_template(db: DB, _user: OwnerUser):
    return Response(
        opening_import.template_xlsx(db),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="opening-balance.xlsx"'},
    )


@router.post("/opening")
async def opening_preview(request: Request, db: DB, user: OwnerUser, file: UploadFile = File(...)):
    filename = file.filename or ""
    content = await file.read(product_import.MAX_FILE_BYTES + 1)
    ctx = {"user": user, "report": None, "error": None, "filename": filename}
    if not filename.lower().endswith(".xlsx"):
        ctx["error"] = "รองรับเฉพาะไฟล์ .xlsx (ดาวน์โหลดไฟล์ตัวอย่างแล้วกรอก)"
        return templates.TemplateResponse(request, "stock/opening.html", ctx, status_code=400)
    try:
        rows = _read_opening(filename, content)
    except product_import.ImportFileError as e:
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "stock/opening.html", ctx, status_code=400)
    ctx["report"] = opening_import.run_opening(db, user, rows, commit=False)
    ctx["token"] = secrets.token_hex(16)
    _opening_path(request, ctx["token"]).write_bytes(content)
    return templates.TemplateResponse(request, "stock/opening.html", ctx)


@router.post("/opening/confirm")
def opening_confirm(request: Request, db: DB, user: OwnerUser, token: Annotated[str, Form()]):
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=400)
    path = _opening_path(request, token)
    if not path.exists():
        return templates.TemplateResponse(request, "stock/opening.html", {
            "user": user, "report": None, "error": "ไม่พบไฟล์ที่อัปโหลดไว้ (อาจนำเข้าไปแล้ว) — กรุณาอัปโหลดใหม่",
        }, status_code=400)
    report = opening_import.run_opening(db, user, _read_opening("file.xlsx", path.read_bytes()), commit=True)
    path.unlink(missing_ok=True)
    return templates.TemplateResponse(request, "stock/opening.html",
                                      {"user": user, "report": report, "error": None, "done": True})


# --- stock card + adjustments ------------------------------------------------------


def _product(db, product_id: int):
    try:
        return catalog.get_product(db, product_id)
    except catalog.CatalogError:
        raise HTTPException(status_code=404) from None


def _render_card(request, db, user, product, error=None, done=None, status_code=200):
    moves = stock.movements_of(db, product.id)
    sale_ids = {m.ref_id for m, _ in moves if m.ref_type == "sale"}
    line_ids = {m.ref_id for m, _ in moves if m.ref_type in (costing.REF_PURCHASE_LINE, costing.REF_PURCHASE_VOID)}
    sales_map = {i: d for i, d in db.execute(select(Sale.id, Sale.doc_no).where(Sale.id.in_(sale_ids)))}
    lines_map = {
        lid: (pid, doc) for lid, pid, doc in db.execute(
            select(PurchaseLine.id, Purchase.id, Purchase.doc_no)
            .join(Purchase, Purchase.id == PurchaseLine.purchase_id).where(PurchaseLine.id.in_(line_ids))
        )
    }
    user_ids = {m.user_id for m, _ in moves if m.user_id}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(user_ids)))}
    return templates.TemplateResponse(request, "stock/card.html", {
        "user": user, "product": product, "moves": moves, "balance": stock.balance_of(db, product.id),
        "labels": MOVE_LABELS, "sales_map": sales_map, "lines_map": lines_map, "names": names,
        "to_local": to_local, "kinds": stock.WRITEOFF_KINDS, "error": error, "done": done,
    }, status_code=status_code)


@router.get("/{product_id}")
def stock_card(request: Request, db: DB, user: CurrentUser, product_id: int, done: str | None = None):
    return _render_card(request, db, user, _product(db, product_id), done=done)


@router.post("/{product_id}/count")
def stock_count(request: Request, db: DB, user: OwnerUser, product_id: int,
                counted: Annotated[str, Form()] = "", reason: Annotated[str, Form()] = ""):
    product = _product(db, product_id)
    try:
        stock.adjust_to_count(db, user, product, counted, reason)
        db.commit()
    except stock.StockError as e:
        db.rollback()
        return _render_card(request, db, user, _product(db, product_id), error=str(e), status_code=400)
    return RedirectResponse(f"/stock/{product_id}?done=adjusted", status_code=303)


@router.post("/{product_id}/writeoff")
def stock_writeoff(request: Request, db: DB, user: OwnerUser, product_id: int, qty: Annotated[str, Form()] = "",
                   kind: Annotated[str, Form()] = "damaged", reason: Annotated[str, Form()] = ""):
    product = _product(db, product_id)
    try:
        stock.write_off(db, user, product, qty, kind, reason)
        db.commit()
    except stock.StockError as e:
        db.rollback()
        return _render_card(request, db, user, _product(db, product_id), error=str(e), status_code=400)
    return RedirectResponse(f"/stock/{product_id}?done=adjusted", status_code=303)
