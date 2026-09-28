import pytest
from sqlalchemy import select

from app.models import AuditLog, User
from app.models.user import ROLE_OWNER
from app.services import auth
from tests.conftest import CASHIER_PIN, OWNER_PIN, login

# --- service ---------------------------------------------------------------


def test_deactivate_and_reactivate(db, owner, cashier):
    auth.set_user_active(db, owner, cashier, False)
    assert not cashier.is_active
    auth.set_user_active(db, owner, cashier, True)
    assert cashier.is_active
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == cashier.id)).all()
    assert "user_deactivate" in actions and "user_activate" in actions


def test_cannot_deactivate_self(db, owner):
    with pytest.raises(auth.AuthError):
        auth.set_user_active(db, owner, owner, False)


def test_cannot_deactivate_last_owner(db, owner):
    other = auth.create_user(db, owner.id, "เจ้าของ 2", ROLE_OWNER, "9999")
    auth.set_user_active(db, other, owner, False)  # two owners -> allowed
    with pytest.raises(auth.AuthError):
        auth.set_user_active(db, owner, other, False)


def test_get_user_missing(db):
    with pytest.raises(auth.AuthError):
        auth.get_user(db, 999)


# --- routes ----------------------------------------------------------------


def test_cashier_cannot_manage_users(client, cashier):
    login(client, cashier, CASHIER_PIN)
    assert client.get("/users").status_code == 403
    assert client.post("/users", data={"name": "x", "pin": "1111", "pin_confirm": "1111"}).status_code == 403
    assert client.post(f"/users/{cashier.id}/pin", data={"pin": "1111", "pin_confirm": "1111"}).status_code == 403


def test_owner_adds_cashier(client, db, owner):
    login(client, owner, OWNER_PIN)
    r = client.post(
        "/users",
        data={"name": "สมหญิง", "role": "cashier", "pin": "2468", "pin_confirm": "2468"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    new = db.scalars(select(User).where(User.name == "สมหญิง")).one()
    assert new.role == "cashier"
    assert "สมหญิง" in client.get("/users").text
    assert auth.authenticate(db, new.id, "2468") is not None


def test_add_user_errors(client, owner):
    login(client, owner, OWNER_PIN)
    r = client.post("/users", data={"name": "ก", "role": "cashier", "pin": "2468", "pin_confirm": "1357"})
    assert r.status_code == 400 and "ไม่ตรงกัน" in r.text
    r = client.post("/users", data={"name": "เจ้าของ", "role": "cashier", "pin": "2468", "pin_confirm": "2468"})
    assert r.status_code == 400 and "มีผู้ใช้ชื่อนี้แล้ว" in r.text


def test_owner_deactivates_cashier_route(client, db, owner, cashier):
    login(client, owner, OWNER_PIN)
    r = client.post(f"/users/{cashier.id}/active", data={"active": "false"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert not db.get(User, cashier.id).is_active
    r = client.post(f"/users/{owner.id}/active", data={"active": "false"})
    assert r.status_code == 400


def test_owner_resets_cashier_pin_route(client, db, owner, cashier):
    login(client, owner, OWNER_PIN)
    r = client.post(f"/users/{cashier.id}/pin", data={"pin": "7777", "pin_confirm": "7777"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert auth.authenticate(db, cashier.id, "7777") is not None


def test_owner_unlocks_route(client, db, owner, cashier):
    for _ in range(auth.MAX_PIN_ATTEMPTS):
        auth.authenticate(db, cashier.id, "0000")
    db.commit()
    login(client, owner, OWNER_PIN)
    assert "ล็อก" in client.get("/users").text
    assert client.post(f"/users/{cashier.id}/unlock", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, cashier.id).locked_until is None
