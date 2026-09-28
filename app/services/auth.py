"""Users, PIN hashing and authentication."""

import math
import re
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.models.base import utcnow
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


# --- PIN lockout -------------------------------------------------------------
# After MAX_PIN_ATTEMPTS consecutive wrong PINs the user is locked for
# LOCK_BASE_SECONDS. Every further wrong PIN (once the lock expires) locks
# again immediately with the duration doubled, capped at LOCK_MAX_SECONDS.
# A correct PIN, an owner unlock or a PIN reset clears the counter.
MAX_PIN_ATTEMPTS = 5
LOCK_BASE_SECONDS = 60
LOCK_MAX_SECONDS = 15 * 60


class LoginLocked(AuthError):
    def __init__(self, seconds_left: int):
        self.seconds_left = seconds_left
        minutes, seconds = divmod(seconds_left, 60)
        wait = f"{minutes} นาที {seconds} วินาที" if minutes else f"{seconds} วินาที"
        super().__init__(f"ใส่ PIN ผิดหลายครั้ง ระบบล็อกชั่วคราว กรุณารอ {wait}")


def lock_seconds_left(user: User, now: datetime | None = None) -> int:
    now = now or utcnow()
    if user.locked_until is None or user.locked_until <= now:
        return 0
    return math.ceil((user.locked_until - now).total_seconds())


def _check_not_locked(user: User, now: datetime) -> None:
    left = lock_seconds_left(user, now)
    if left:
        raise LoginLocked(left)


def _register_failure(db: Session, user: User, now: datetime, action: str) -> None:
    user.failed_pin_count += 1
    log_action(db, user.id, action, "user", user.id, {"failed_count": user.failed_pin_count})
    over = user.failed_pin_count - MAX_PIN_ATTEMPTS
    if over >= 0:
        seconds = min(LOCK_BASE_SECONDS * 2**over, LOCK_MAX_SECONDS)
        user.locked_until = now + timedelta(seconds=seconds)
        log_action(db, user.id, "pin_locked", "user", user.id, {"seconds": seconds})


def _clear_failures(user: User) -> None:
    user.failed_pin_count = 0
    user.locked_until = None


def authenticate(db: Session, user_id: int, pin: str, now: datetime | None = None) -> User | None:
    """Check a user's PIN. Returns None on a wrong PIN, raises LoginLocked
    while locked (the PIN is not checked then). Caller commits."""
    now = now or utcnow()
    user = get_active_user(db, user_id)
    if user is None:
        return None
    _check_not_locked(user, now)
    if not verify_pin(user.pin_hash, pin):
        _register_failure(db, user, now, "login_failed")
        return None
    _clear_failures(user)
    log_action(db, user.id, "login", "user", user.id)
    return user


def verify_owner_pin(db: Session, pin: str, now: datetime | None = None) -> User | None:
    """Return the active, unlocked owner whose PIN matches (owner-approval prompts).

    A wrong PIN counts as a failed attempt against every unlocked owner, since
    the caller was trying to guess an owner PIN. Caller commits.
    """
    now = now or utcnow()
    owners = [
        o
        for o in db.scalars(select(User).where(User.role == ROLE_OWNER, User.is_active.is_(True)))
        if not lock_seconds_left(o, now)
    ]
    for owner in owners:
        if verify_pin(owner.pin_hash, pin):
            _clear_failures(owner)
            return owner
    for owner in owners:
        _register_failure(db, owner, now, "owner_pin_failed")
    return None


def reset_pin(db: Session, actor_id: int | None, user: User, new_pin: str, via: str) -> None:
    user.pin_hash = hash_pin(new_pin)
    _clear_failures(user)
    log_action(db, actor_id, "pin_reset", "user", user.id, {"via": via})


# --- user management (owner only; enforced by routes) -------------------------


def list_all_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.is_active.desc(), User.role.desc(), User.name)))


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AuthError("ไม่พบผู้ใช้")
    return user


def set_user_active(db: Session, actor: User, user: User, active: bool) -> None:
    if user.is_active == active:
        return
    if not active:
        if user.id == actor.id:
            raise AuthError("ปิดใช้งานบัญชีของตัวเองไม่ได้")
        if user.role == ROLE_OWNER and _active_owner_count(db) <= 1:
            raise AuthError("ต้องมีเจ้าของร้านที่ใช้งานได้อย่างน้อย 1 คน")
    user.is_active = active
    log_action(
        db, actor.id, "user_activate" if active else "user_deactivate", "user", user.id,
        {"name": user.name},
    )


def unlock_user(db: Session, actor: User, user: User) -> None:
    _clear_failures(user)
    log_action(db, actor.id, "pin_unlock", "user", user.id, {"name": user.name})


def _active_owner_count(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == ROLE_OWNER, User.is_active.is_(True))
    )
