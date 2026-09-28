"""Users, PIN hashing and authentication."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.models.user import ROLE_OWNER, ROLES
from app.services.audit import log_action

_PIN_RE = re.compile(r"^[0-9]{4,6}$")
_hasher = PasswordHasher()


class AuthError(ValueError):
    """Raised with a Thai message that can be shown to the user."""


def validate_pin(pin: str) -> None:
    if not _PIN_RE.match(pin or ""):
        raise AuthError("PIN ต้องเป็นตัวเลข 4–6 หลัก")


def hash_pin(pin: str) -> str:
    validate_pin(pin)
    return _hasher.hash(pin)


def verify_pin(pin_hash: str, pin: str) -> bool:
    try:
        return _hasher.verify(pin_hash, pin)
    except (VerificationError, InvalidHashError):
        return False


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise AuthError("กรุณากรอกชื่อ")
    if len(name) > 100:
        raise AuthError("ชื่อยาวเกิน 100 ตัวอักษร")
    return name


def needs_setup(db: Session) -> bool:
    """True until the first owner has been created."""
    return db.scalar(select(func.count()).select_from(User)) == 0


def list_login_users(db: Session) -> list[User]:
    return list(
        db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.role.desc(), User.name))
    )


def get_active_user(db: Session, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user if user is not None and user.is_active else None


def create_user(db: Session, actor_id: int | None, name: str, role: str, pin: str) -> User:
    name = _clean_name(name)
    if role not in ROLES:
        raise AuthError("สิทธิ์ผู้ใช้ไม่ถูกต้อง")
    if db.scalar(select(User.id).where(User.name == name)) is not None:
        raise AuthError("มีผู้ใช้ชื่อนี้แล้ว")
    user = User(name=name, role=role, pin_hash=hash_pin(pin), is_active=True)
    db.add(user)
    db.flush()
    log_action(db, actor_id or user.id, "user_create", "user", user.id, {"name": name, "role": role})
    return user


def create_first_owner(db: Session, name: str, pin: str) -> User:
    if not needs_setup(db):
        raise AuthError("ตั้งค่าเจ้าของร้านไปแล้ว")
    return create_user(db, None, name, ROLE_OWNER, pin)


def authenticate(db: Session, user_id: int, pin: str) -> User | None:
    """Check a user's PIN. Logs both successful and failed attempts."""
    user = get_active_user(db, user_id)
    if user is None:
        return None
    if not verify_pin(user.pin_hash, pin):
        log_action(db, user.id, "login_failed", "user", user.id)
        return None
    log_action(db, user.id, "login", "user", user.id)
    return user


def verify_owner_pin(db: Session, pin: str) -> User | None:
    """Return the active owner whose PIN matches (for owner-approval prompts)."""
    owners = db.scalars(select(User).where(User.role == ROLE_OWNER, User.is_active.is_(True)))
    for owner in owners:
        if verify_pin(owner.pin_hash, pin):
            return owner
    return None


def reset_pin(db: Session, actor_id: int | None, user: User, new_pin: str, via: str) -> None:
    user.pin_hash = hash_pin(new_pin)
    log_action(db, actor_id, "pin_reset", "user", user.id, {"via": via})
