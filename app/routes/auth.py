from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.routes.deps import DB, CurrentUser
from app.services import auth
from app.templating import templates

router = APIRouter()


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/setup")
def setup_form(request: Request, db: DB):
    if not auth.needs_setup(db):
        return _redirect("/login")
    return templates.TemplateResponse(request, "auth/setup.html", {"error": None, "name": ""})


@router.post("/setup")
def setup_submit(
    request: Request,
    db: DB,
    name: Annotated[str, Form()] = "",
    pin: Annotated[str, Form()] = "",
    pin_confirm: Annotated[str, Form()] = "",
):
    if not auth.needs_setup(db):
        return _redirect("/login")
    try:
        if pin != pin_confirm:
            raise auth.AuthError("PIN ทั้งสองช่องไม่ตรงกัน")
        owner = auth.create_first_owner(db, name, pin)
        db.commit()
    except auth.AuthError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "auth/setup.html", {"error": str(e), "name": name}, status_code=400
        )
    request.session.clear()
    request.session["user_id"] = owner.id
    return _redirect("/settings")


@router.get("/login")
def login_form(request: Request, db: DB):
    if auth.needs_setup(db):
        return _redirect("/setup")
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"users": auth.list_login_users(db), "error": None, "selected_id": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    db: DB,
    user_id: Annotated[int, Form()],
    pin: Annotated[str, Form()] = "",
):
    user = auth.authenticate(db, user_id, pin)
    db.commit()  # persist login / login_failed audit entries
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"users": auth.list_login_users(db), "error": "PIN ไม่ถูกต้อง", "selected_id": user_id},
            status_code=400,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return _redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


@router.get("/")
def home(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request, "home.html", {"user": user})
