from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.models import User
from app.services import auth


class LoginRequired(Exception):
    """Handled in main.py: redirect to /login (or /setup on first run)."""


class OwnerRequired(Exception):
    """Handled in main.py: 403 page."""


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DB) -> User:
    user = auth.get_active_user(db, request.session.get("user_id"))
    if user is None:
        request.session.clear()
        raise LoginRequired()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_owner(user: CurrentUser) -> User:
    if not user.is_owner:
        raise OwnerRequired()
    return user


OwnerUser = Annotated[User, Depends(require_owner)]


def require_receiver(user: CurrentUser) -> User:
    """Owner, or staff the owner allowed to receive goods."""
    if not user.may_receive_stock:
        raise OwnerRequired()
    return user


ReceiverUser = Annotated[User, Depends(require_receiver)]
