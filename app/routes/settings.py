from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.models.settings import HEAD_OFFICE_BRANCH_NO
from app.routes.deps import DB, CurrentUser, OwnerUser
from app.services import settings as settings_service
from app.services.settings import SettingsError, ShopSettingsInput
from app.templating import templates

router = APIRouter()


def _form_from_row(row) -> ShopSettingsInput:
    is_head = row.branch_no in (None, HEAD_OFFICE_BRANCH_NO)
    return ShopSettingsInput(
        shop_name=row.shop_name,
        address=row.address,
        phone=row.phone,
        vat_mode=row.vat_mode,
        tax_id=row.tax_id or "",
        branch_type="head" if is_head else "branch",
        branch_no="" if is_head else row.branch_no,
        vat_rate=settings_service.format_vat_rate(row.vat_rate_bp),
        price_includes_vat=row.price_includes_vat,
        allow_abbreviated_invoice=row.allow_abbreviated_invoice,
        backup_dir=row.backup_dir or "",
        backup_keep_days=str(row.backup_keep_days),
    )


def _render(request: Request, user, row, form, errors=None, saved=False, status_code=200):
    return templates.TemplateResponse(
        request,
        "backoffice/settings.html",
        {
            "user": user,
            "row": row,
            "form": form,
            "errors": errors or {},
            "saved": saved,
            "default_backup_dir": str(request.app.state.config.default_backup_dir),
        },
        status_code=status_code,
    )


@router.get("/settings")
def settings_form(request: Request, db: DB, user: OwnerUser, saved: bool = False):
    row = settings_service.get_shop_settings(db)
    return _render(request, user, row, _form_from_row(row), saved=saved)


@router.post("/settings")
def settings_submit(
    request: Request,
    db: DB,
    user: OwnerUser,
    shop_name: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    vat_mode: Annotated[str, Form()] = "none",
    tax_id: Annotated[str, Form()] = "",
    branch_type: Annotated[str, Form()] = "head",
    branch_no: Annotated[str, Form()] = "",
    vat_rate: Annotated[str, Form()] = "7.00",
    price_includes_vat: Annotated[bool, Form()] = False,
    allow_abbreviated_invoice: Annotated[bool, Form()] = False,
    backup_dir: Annotated[str, Form()] = "",
    backup_keep_days: Annotated[str, Form()] = "30",
):
    form = ShopSettingsInput(
        shop_name=shop_name,
        address=address,
        phone=phone,
        vat_mode=vat_mode,
        tax_id=tax_id,
        branch_type=branch_type,
        branch_no=branch_no,
        vat_rate=vat_rate,
        price_includes_vat=price_includes_vat,
        allow_abbreviated_invoice=allow_abbreviated_invoice,
        backup_dir=backup_dir,
        backup_keep_days=backup_keep_days,
    )
    try:
        settings_service.update_shop_settings(db, user.id, form)
        db.commit()
    except SettingsError as e:
        db.rollback()
        row = settings_service.get_shop_settings(db)
        return _render(request, user, row, form, errors=e.errors, status_code=400)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/logo")
async def settings_logo(request: Request, db: DB, user: OwnerUser, logo: UploadFile = File(...)):
    content = await logo.read(settings_service.MAX_LOGO_BYTES + 1)
    try:
        settings_service.save_logo(db, user.id, request.app.state.config.uploads_dir, content)
        db.commit()
    except SettingsError as e:
        db.rollback()
        row = settings_service.get_shop_settings(db)
        return _render(request, user, row, _form_from_row(row), errors=e.errors, status_code=400)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.get("/settings/logo")
def settings_logo_file(request: Request, db: DB, _user: CurrentUser):
    row = settings_service.get_shop_settings(db)
    if not row.logo_filename:
        raise HTTPException(status_code=404)
    path = request.app.state.config.uploads_dir / row.logo_filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
