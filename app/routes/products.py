import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.routes.deps import DB, CurrentUser, OwnerUser
from app.services import catalog, product_import
from app.services.catalog import CatalogError, ProductInput, UnitInput
from app.services.money import format_money_input, format_qty_input
from app.templating import templates

router = APIRouter(prefix="/products")

PAGE_SIZE = 50
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


# --- list ----------------------------------------------------------------------


@router.get("")
def product_list(
    request: Request,
    db: DB,
    user: CurrentUser,
    q: str = "",
    category_id: str = "",
    show_inactive: bool = False,
    page: int = 1,
    done: str | None = None,
):
    page = max(1, page)
    cat_id = int(category_id) if category_id.isdigit() else None
    products, total = catalog.search_products(
        db, q, cat_id, include_inactive=show_inactive and user.is_owner,
        limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
    )
    return templates.TemplateResponse(
        request,
        "backoffice/products_list.html",
        {
            "user": user,
            "products": products,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)),
            "q": q,
            "category_id": cat_id,
            "show_inactive": show_inactive,
            "categories": catalog.list_categories(db),
            "done": done,
        },
    )


# --- create / edit ---------------------------------------------------------------


def _form_from_product(p) -> dict:
    extras = [u for u in p.units if u is not p.base_unit_row]
    default = p.default_unit
    return {
        "sku": p.sku,
        "barcode": p.barcode or "",
        "name": p.name,
        "category_id": p.category_id,
        "base_unit": p.base_unit,
        "base_price": format_money_input(p.base_unit_row.price if p.base_unit_row else 0),
        "vat_exempt": p.vat_exempt,
        "min_stock": format_qty_input(p.min_stock_qty),
        "default": "base" if default is None or default is p.base_unit_row else str(extras.index(default)),
        "units": [
            {"name": u.unit_name, "factor": format_qty_input(u.factor_to_base),
             "price": format_money_input(u.price), "barcode": u.barcode or ""}
            for u in extras
        ],
    }


def _empty_form() -> dict:
    return {"sku": "", "barcode": "", "name": "", "category_id": None, "base_unit": "", "base_price": "",
            "vat_exempt": False, "min_stock": "0", "default": "base", "units": []}


def _render_form(request, db, user, form, product=None, errors=None, message=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "backoffice/product_form.html",
        {
            "user": user,
            "form": form,
            "product": product,
            "errors": errors or {},
            "message": message,
            "categories": catalog.list_categories(db, include_inactive=False),
        },
        status_code=status_code,
    )


class _ProductForm:
    """Reads the product form (unit rows arrive as parallel lists)."""

    def __init__(
        self,
        name: Annotated[str, Form()] = "",
        sku: Annotated[str, Form()] = "",
        barcode: Annotated[str, Form()] = "",
        category_id: Annotated[str, Form()] = "",
        base_unit: Annotated[str, Form()] = "",
        base_price: Annotated[str, Form()] = "",
        vat_exempt: Annotated[bool, Form()] = False,
        min_stock: Annotated[str, Form()] = "0",
        default_unit: Annotated[str, Form()] = "base",
        unit_name: Annotated[list[str], Form()] = [],  # noqa: B006
        unit_factor: Annotated[list[str], Form()] = [],  # noqa: B006
        unit_price: Annotated[list[str], Form()] = [],  # noqa: B006
        unit_barcode: Annotated[list[str], Form()] = [],  # noqa: B006
    ):
        n = len(unit_name)
        pad = lambda xs: list(xs) + [""] * (n - len(xs))  # noqa: E731
        self.units = [
            {"name": a, "factor": b, "price": c, "barcode": d}
            for a, b, c, d in zip(unit_name, pad(unit_factor), pad(unit_price), pad(unit_barcode))
        ]
        self.form = {
            "sku": sku, "barcode": barcode, "name": name,
            "category_id": int(category_id) if category_id.isdigit() else None,
            "base_unit": base_unit, "base_price": base_price, "vat_exempt": vat_exempt,
            "min_stock": min_stock, "default": default_unit, "units": self.units,
        }

    def to_input(self) -> ProductInput:
        f = self.form
        default_name = ""
        if f["default"].isdigit() and int(f["default"]) < len(self.units):
            default_name = self.units[int(f["default"])]["name"]
        return ProductInput(
            name=f["name"], sku=f["sku"], barcode=f["barcode"], category_id=f["category_id"],
            base_unit=f["base_unit"], base_price=f["base_price"], vat_exempt=f["vat_exempt"],
            min_stock=f["min_stock"], default_unit=default_name,
            units=[UnitInput(u["name"], u["factor"], u["price"], u["barcode"]) for u in self.units],
        )




@router.get("/new")
def product_new(request: Request, db: DB, user: OwnerUser):
    return _render_form(request, db, user, _empty_form())


@router.post("")
def product_create(request: Request, db: DB, user: OwnerUser, data: Annotated[_ProductForm, Depends()]):
    try:
        product = catalog.create_product(db, user.id, data.to_input())
        db.commit()
    except CatalogError as e:
        db.rollback()
        return _render_form(request, db, user, data.form, errors=e.errors, status_code=400)
    return RedirectResponse(f"/products/{product.id}?done=created", status_code=303)


# --- import (declared before /{product_id}) ------------------------------------


@router.get("/import")
def import_page(request: Request, user: OwnerUser):
    return templates.TemplateResponse(
        request, "backoffice/product_import.html", {"user": user, "report": None, "error": None, "token": None}
    )


@router.get("/import/template.xlsx")
def import_template_xlsx(_user: OwnerUser):
    return Response(
        product_import.template_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="product-template.xlsx"'},
    )


@router.get("/import/template.csv")
def import_template_csv(_user: OwnerUser):
    return Response(
        product_import.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="product-template.csv"'},
    )


def _import_path(request: Request, token: str, ext: str):
    return request.app.state.config.uploads_dir / f"import-{token}.{ext}"


@router.post("/import")
async def import_preview(request: Request, db: DB, user: OwnerUser, file: UploadFile = File(...)):
    filename = file.filename or ""
    content = await file.read(product_import.MAX_FILE_BYTES + 1)
    ctx = {"user": user, "report": None, "error": None, "token": None, "filename": filename}
    try:
        rows = product_import.read_rows(filename, content)
    except product_import.ImportFileError as e:
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "backoffice/product_import.html", ctx, status_code=400)
    ctx["report"] = product_import.run_import(db, user.id, rows, commit=False)
    token = secrets.token_hex(16)
    ext = "xlsx" if filename.lower().endswith(".xlsx") else "csv"
    _import_path(request, token, ext).write_bytes(content)
    ctx["token"], ctx["ext"] = token, ext
    return templates.TemplateResponse(request, "backoffice/product_import.html", ctx)


@router.post("/import/confirm")
def import_confirm(
    request: Request,
    db: DB,
    user: OwnerUser,
    token: Annotated[str, Form()],
    ext: Annotated[str, Form()],
):
    if not _TOKEN_RE.match(token) or ext not in ("xlsx", "csv"):
        raise HTTPException(status_code=400)
    path = _import_path(request, token, ext)
    if not path.exists():
        return templates.TemplateResponse(
            request, "backoffice/product_import.html",
            {"user": user, "report": None, "token": None,
             "error": "ไม่พบไฟล์ที่อัปโหลดไว้ (อาจนำเข้าไปแล้ว) — กรุณาอัปโหลดใหม่"},
            status_code=400,
        )
    rows = product_import.read_rows(f"file.{ext}", path.read_bytes())
    report = product_import.run_import(db, user.id, rows, commit=True)
    path.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request, "backoffice/product_import.html",
        {"user": user, "report": report, "error": None, "token": None, "done": True},
    )


# --- single product ------------------------------------------------------------


def _get_or_404(db, product_id: int):
    try:
        return catalog.get_product(db, product_id)
    except CatalogError:
        raise HTTPException(status_code=404) from None


@router.get("/{product_id}")
def product_detail(request: Request, db: DB, user: CurrentUser, product_id: int, done: str | None = None):
    product = _get_or_404(db, product_id)
    if not user.is_owner:
        return templates.TemplateResponse(
            request, "backoffice/product_view.html", {"user": user, "product": product}
        )
    messages = {"created": "เพิ่มสินค้าเรียบร้อยแล้ว", "saved": "บันทึกเรียบร้อยแล้ว",
                "alias": "เพิ่มชื่อเรียกแล้ว", "status": "เปลี่ยนสถานะแล้ว"}
    return _render_form(request, db, user, _form_from_product(product), product, message=messages.get(done))


@router.post("/{product_id}")
def product_update(
    request: Request, db: DB, user: OwnerUser, product_id: int, data: Annotated[_ProductForm, Depends()]
):
    product = _get_or_404(db, product_id)
    try:
        catalog.update_product(db, user.id, product, data.to_input())
        db.commit()
    except CatalogError as e:
        db.rollback()
        product = _get_or_404(db, product_id)
        return _render_form(request, db, user, data.form, product, errors=e.errors, status_code=400)
    return RedirectResponse(f"/products/{product_id}?done=saved", status_code=303)


@router.post("/{product_id}/active")
def product_set_active(db: DB, user: OwnerUser, product_id: int, active: Annotated[bool, Form()]):
    catalog.set_product_active(db, user.id, _get_or_404(db, product_id), active)
    db.commit()
    return RedirectResponse(f"/products/{product_id}?done=status", status_code=303)


@router.post("/{product_id}/aliases")
def product_add_alias(
    request: Request, db: DB, user: OwnerUser, product_id: int, alias: Annotated[str, Form()] = ""
):
    product = _get_or_404(db, product_id)
    try:
        _, warning = catalog.add_alias(db, user.id, product, alias)
        db.commit()
    except CatalogError as e:
        db.rollback()
        product = _get_or_404(db, product_id)
        return _render_form(request, db, user, _form_from_product(product), product,
                            errors=e.errors, status_code=400)
    if warning:
        product = _get_or_404(db, product_id)
        return _render_form(request, db, user, _form_from_product(product), product,
                            message="เพิ่มชื่อเรียกแล้ว", errors={"alias_warning": warning})
    return RedirectResponse(f"/products/{product_id}?done=alias#aliases", status_code=303)
