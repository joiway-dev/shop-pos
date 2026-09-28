from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.models.base import utcnow
from app.services import auth
from tests.conftest import CASHIER_PIN, OWNER_PIN, login

WRONG = "0000"


def fail_n(db, user, n, now):
    for _ in range(n):
        assert auth.authenticate(db, user.id, WRONG, now=now) is None


def test_locks_after_max_attempts(db, cashier):
    now = utcnow()
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS - 1, now)
    assert cashier.locked_until is None
    fail_n(db, cashier, 1, now)
    assert auth.lock_seconds_left(cashier, now) == auth.LOCK_BASE_SECONDS


def test_locked_user_rejected_even_with_correct_pin(db, cashier):
    now = utcnow()
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, now)
    with pytest.raises(auth.LoginLocked) as e:
        auth.authenticate(db, cashier.id, CASHIER_PIN, now=now + timedelta(seconds=10))
    assert e.value.seconds_left == auth.LOCK_BASE_SECONDS - 10
    # Attempts while locked do not add to the counter.
    assert cashier.failed_pin_count == auth.MAX_PIN_ATTEMPTS


def test_lock_expires_and_success_resets(db, cashier):
    now = utcnow()
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, now)
    later = now + timedelta(seconds=auth.LOCK_BASE_SECONDS)
    assert auth.authenticate(db, cashier.id, CASHIER_PIN, now=later) is not None
    assert cashier.failed_pin_count == 0
    assert cashier.locked_until is None


def test_lock_duration_doubles_and_caps(db, cashier):
    now = utcnow()
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, now)
    durations = []
    for _ in range(6):
        now = cashier.locked_until
        fail_n(db, cashier, 1, now)
        durations.append(auth.lock_seconds_left(cashier, now))
    assert durations == [120, 240, 480, 900, 900, 900]


def test_failures_are_audited(db, cashier):
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, utcnow())
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == cashier.id)).all()
    assert actions.count("login_failed") == auth.MAX_PIN_ATTEMPTS
    assert actions.count("pin_locked") == 1


def test_reset_pin_and_unlock_clear_lock(db, owner, cashier):
    now = utcnow()
    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, now)
    auth.unlock_user(db, owner, cashier)
    assert auth.lock_seconds_left(cashier, now) == 0 and cashier.failed_pin_count == 0

    fail_n(db, cashier, auth.MAX_PIN_ATTEMPTS, now)
    auth.reset_pin(db, owner.id, cashier, "4444", via="test")
    assert auth.authenticate(db, cashier.id, "4444", now=now) is not None


def test_owner_pin_guessing_locks_owner(db, owner, cashier):
    now = utcnow()
    for _ in range(auth.MAX_PIN_ATTEMPTS):
        assert auth.verify_owner_pin(db, CASHIER_PIN, now=now) is None
    assert auth.lock_seconds_left(owner, now) > 0
    # Locked owner cannot approve even with the right PIN.
    assert auth.verify_owner_pin(db, OWNER_PIN, now=now) is None
    later = owner.locked_until
    assert auth.verify_owner_pin(db, OWNER_PIN, now=later).id == owner.id
    assert owner.failed_pin_count == 0


def test_login_route_shows_lock_message(client, cashier):
    for _ in range(auth.MAX_PIN_ATTEMPTS):
        assert login(client, cashier, WRONG).status_code == 400
    r = login(client, cashier, CASHIER_PIN)
    assert r.status_code == 400
    assert "ล็อกชั่วคราว" in r.text
