from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.deps import DB, OwnerUser
from app.services import auth
from app.templating import templates

router = APIRouter(prefix="/users")

_MESSAGES = {
    "created": "เพิ่มผู้ใช้เรียบร้อยแล้ว",
    "activated": "เปิดใช้งานผู้ใช้แล้ว",
    "deactivated": "ปิดใช้งานผู้ใช้แล้ว",
    "unlocked": "ปลดล็อกแล้ว",
    "pin": "เปลี่ยน PIN เรียบร้อยแล้ว",
    "permissions": "บันทึกสิทธิ์แล้ว",
}


def _render(request: Request, db, user, error=None, form=None, message=None, status_code=200):
    users = auth.list_all_users(db)
    return templates.TemplateResponse(
        request,
        "backoffice/users.html",
        {
            "user": user,
            "users": users,
            "locked": {u.id: auth.lock_seconds_left(u) for u in users},
            "error": error,
            "message": _MESSAGES.get(message),
            "form": form or {"name": "", "role": "cashier"},
        },
        status_code=status_code,
    )


def _done(message: str) -> RedirectResponse:
    return RedirectResponse(f"/users?done={message}", status_code=303)


@router.get("")
def users_page(request: Request, db: DB, user: OwnerUser, done: str | None = None):
    return _render(request, db, user, message=done)


@router.post("")
def users_create(
    request: Request,
    db: DB,
    user: OwnerUser,
    name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "cashier",
    pin: Annotated[str, Form()] = "",
    pin_confirm: Annotated[str, Form()] = "",
):
    try:
        if pin != pin_confirm:
            raise auth.AuthError("PIN ทั้งสองช่องไม่ตรงกัน")
        auth.create_user(db, user.id, name, role, pin)
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), form={"name": name, "role": role}, status_code=400)
    return _done("created")


@router.post("/{user_id}/active")
def users_set_active(
    request: Request, db: DB, user: OwnerUser, user_id: int, active: Annotated[bool, Form()]
):
    try:
        auth.set_user_active(db, user, auth.get_user(db, user_id), active)
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return _done("activated" if active else "deactivated")


@router.post("/{user_id}/permissions")
def users_permissions(
    request: Request,
    db: DB,
    user: OwnerUser,
    user_id: int,
    can_receive_stock: Annotated[bool, Form()] = False,
    can_see_cost: Annotated[bool, Form()] = False,
):
    try:
        auth.set_permissions(db, user, auth.get_user(db, user_id), can_receive_stock, can_see_cost)
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return _done("permissions")


@router.post("/{user_id}/unlock")
def users_unlock(request: Request, db: DB, user: OwnerUser, user_id: int):
    try:
        auth.unlock_user(db, user, auth.get_user(db, user_id))
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return _done("unlocked")


@router.post("/{user_id}/pin")
def users_reset_pin(
    request: Request,
    db: DB,
    user: OwnerUser,
    user_id: int,
    pin: Annotated[str, Form()] = "",
    pin_confirm: Annotated[str, Form()] = "",
):
    try:
        if pin != pin_confirm:
            raise auth.AuthError("PIN ทั้งสองช่องไม่ตรงกัน")
        auth.reset_pin(db, user.id, auth.get_user(db, user_id), pin, via="users page")
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return _render(request, db, user, error=str(e), status_code=400)
    return _done("pin")
