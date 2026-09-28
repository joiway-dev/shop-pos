import logging
import threading
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models import User
from app.routes.deps import DB, CurrentUser
from app.services import backup, closing
from app.services.clock import now_local, to_local
from app.services.closing import CloseError
from app.services.money import format_money_input
from app.templating import templates

router = APIRouter(prefix="/close")
log = logging.getLogger("shop")


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text) if text else now_local().date()
    except ValueError:
        return now_local().date()


def _render(request, db, user, day: date, error=None, done=None, form=None, status_code=200):
    history = closing.closes_of(db, day)
    last_opening = history[0].opening_cash if history else 0
    names = {u.id: u.name for u in db.query(User)}
    return templates.TemplateResponse(request, "reports/close.html", {
        "user": user, "day": day, "p": closing.preview(db, day, last_opening), "history": history,
        "recent": closing.recent_closes(db, 14), "names": names, "to_local": to_local, "error": error,
        "done": done, "form": form or {"opening": format_money_input(last_opening) if last_opening else "", "counted": "",
                                        "note": ""},
    }, status_code=status_code)


@router.get("")
def close_page(request: Request, db: DB, user: CurrentUser, day: str = "", done: str | None = None):
    return _render(request, db, user, _day(day), done=done)


def _backup_after_close(app) -> None:
    try:
        with app.state.session_factory() as db:
            target = backup.backup_dir(db, app.state.config)
        backup.create_backup(app.state.config, target, "close")
    except Exception:
        log.exception("backup after daily close failed")


@router.post("")
def close_submit(
    request: Request, db: DB, user: CurrentUser, day: Annotated[str, Form()] = "",
    opening: Annotated[str, Form()] = "", counted: Annotated[str, Form()] = "", note: Annotated[str, Form()] = "",
):
    d = _day(day)
    try:
        closing.close_day(db, user, d, opening, counted, note)
        db.commit()
    except CloseError as e:
        db.rollback()
        return _render(request, db, user, d, error=str(e), form={"opening": opening, "counted": counted, "note": note},
                       status_code=400)
    threading.Thread(target=_backup_after_close, args=(request.app,), daemon=True).start()
    return RedirectResponse(f"/close?day={d.isoformat()}&done=1", status_code=303)
