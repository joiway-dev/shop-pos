from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.deps import DB, OwnerUser
from app.services import backup
from app.services.backup import GO_LIVE_CONFIRM, REASON_LABELS, BackupError
from app.services.settings import get_shop_settings
from app.templating import templates

router = APIRouter(prefix="/backup")

MESSAGES = {"made": "สำรองข้อมูลแล้ว", "restored": "กู้คืนข้อมูลแล้ว — กรุณาเข้าสู่ระบบใหม่",
            "live": "ล้างข้อมูลทดสอบแล้ว พร้อมใช้งานจริง (ไฟล์สำรองก่อนล้างเก็บไว้ในรายการด้านล่าง)"}


def _render(request, db, user, error=None, done=None, status_code=200):
    config = request.app.state.config
    target = backup.backup_dir(db, config)
    settings = get_shop_settings(db)
    return templates.TemplateResponse(request, "reports/backup.html", {
        "user": user, "target": target, "files": backup.list_backups(target), "labels": REASON_LABELS,
        "keep_days": settings.backup_keep_days, "went_live_at": settings.went_live_at,
        "confirm_word": GO_LIVE_CONFIRM, "error": error, "message": MESSAGES.get(done or ""),
    }, status_code=status_code)


@router.get("")
def backup_page(request: Request, db: DB, user: OwnerUser, done: str | None = None):
    return _render(request, db, user, done=done)


@router.post("/now")
def backup_now(request: Request, db: DB, user: OwnerUser):
    try:
        backup.create_backup(request.app.state.config, backup.backup_dir(db, request.app.state.config), "manual")
    except BackupError as e:
        return _render(request, db, user, error=str(e), status_code=400)
    return RedirectResponse("/backup?done=made", status_code=303)


@router.post("/restore")
def backup_restore(request: Request, db: DB, user: OwnerUser, filename: Annotated[str, Form()] = "",
                   owner_pin: Annotated[str, Form()] = ""):
    try:
        backup.restore(db, request.app.state.config, user, filename, owner_pin, request.app.state.engine)
    except BackupError as e:
        with request.app.state.session_factory() as fresh:
            return _render(request, fresh, user, error=str(e), status_code=400)
    request.session.clear()  # users in the restored data may differ
    return RedirectResponse("/login", status_code=303)


@router.post("/go-live")
def backup_go_live(request: Request, db: DB, user: OwnerUser, owner_pin: Annotated[str, Form()] = "",
                   confirm: Annotated[str, Form()] = ""):
    try:
        backup.go_live(db, request.app.state.config, user, owner_pin, confirm)
    except BackupError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return RedirectResponse("/backup?done=live", status_code=303)
