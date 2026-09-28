import pytest
from sqlalchemy import select

from app.models import AuditLog, User
from app.models.user import ROLE_CASHIER
from app.services import auth
from tests.conftest import CASHIER_PIN, OWNER_PIN, login

# --- service ---------------------------------------------------------------


def test_hash_and_verify_pin():
    h = auth.hash_pin("1234")
    assert h != "1234"
    assert auth.verify_pin(h, "1234")
    assert not auth.verify_pin(h, "4321")
    assert not auth.verify_pin("not-a-hash", "1234")


@pytest.mark.parametrize("pin", ["", "123", "1234567", "12a4", " 1234", "١٢٣٤", "๑๒๓๔"])
def test_invalid_pin_rejected(pin):
    with pytest.raises(auth.AuthError):
        auth.hash_pin(pin)


@pytest.mark.parametrize("pin", ["1234", "12345", "000000"])
def test_valid_pin_lengths(pin):
    auth.validate_pin(pin)


def test_first_owner_only_once(db):
    assert auth.needs_setup(db)
    owner = auth.create_first_owner(db, "  สมชาย   ใจดี ", OWNER_PIN)
    db.commit()
    assert owner.name == "สมชาย ใจดี"
    assert owner.is_owner
    assert not auth.needs_setup(db)
    with pytest.raises(auth.AuthError):
        auth.create_first_owner(db, "อีกคน", "9999")


def test_create_user_audited_and_unique_name(db, owner):
    auth.create_user(db, owner.id, "แดง", ROLE_CASHIER, "1111")
    db.commit()
    log = db.scalars(select(AuditLog).where(AuditLog.action == "user_create")).all()
    assert [entry.detail["name"] for entry in log] == ["เจ้าของ", "แดง"]
    with pytest.raises(auth.AuthError):
        auth.create_user(db, owner.id, "แดง", ROLE_CASHIER, "2222")
    with pytest.raises(auth.AuthError):
        auth.create_user(db, owner.id, "ดำ", "admin", "2222")


def test_authenticate(db, owner):
    assert auth.authenticate(db, owner.id, OWNER_PIN).id == owner.id
    assert auth.authenticate(db, owner.id, "0000") is None
    assert auth.authenticate(db, 999, OWNER_PIN) is None
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity == "user")).all()
    assert "login" in actions and "login_failed" in actions


def test_inactive_user_cannot_log_in(db, cashier):
    cashier.is_active = False
    db.commit()
    assert auth.authenticate(db, cashier.id, CASHIER_PIN) is None
    assert cashier not in auth.list_login_users(db)


def test_verify_owner_pin(db, owner, cashier):
    assert auth.verify_owner_pin(db, OWNER_PIN).id == owner.id
    assert auth.verify_owner_pin(db, CASHIER_PIN) is None


def test_reset_pin(db, owner):
    auth.reset_pin(db, None, owner, "8888", via="test")
    db.commit()
    assert auth.authenticate(db, owner.id, "8888") is not None
    assert auth.authenticate(db, owner.id, OWNER_PIN) is None


# --- routes ----------------------------------------------------------------


def test_first_run_redirects_to_setup(client):
    r = client.get("/", follow_redirects=True)
    assert r.url.path == "/setup"


def test_setup_creates_owner_and_logs_in(client, db):
    r = client.post(
        "/setup", data={"name": "เจ้าของ", "pin": "1234", "pin_confirm": "1234"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert client.get("/").status_code == 200
    assert db.scalars(select(User)).one().is_owner
    # Setup is closed once an owner exists.
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_setup_pin_mismatch(client, db):
    r = client.post("/setup", data={"name": "เจ้าของ", "pin": "1234", "pin_confirm": "1235"})
    assert r.status_code == 400
    assert "ไม่ตรงกัน" in r.text
    assert auth.needs_setup(db)


def test_login_page_lists_users(client, owner, cashier):
    r = client.get("/login")
    assert r.status_code == 200
    assert "เจ้าของ" in r.text and "พนักงาน" in r.text


def test_login_wrong_pin(client, owner):
    r = login(client, owner, "0000")
    assert r.status_code == 400
    assert "PIN ไม่ถูกต้อง" in r.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_login_and_logout(client, owner):
    assert login(client, owner, OWNER_PIN).status_code == 303
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_deactivated_user_session_is_dropped(client, db, cashier):
    login(client, cashier, CASHIER_PIN)
    cashier.is_active = False
    db.commit()
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_htmx_request_gets_hx_redirect(client, owner):
    r = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/login"
