import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.services import settings as svc
from app.services.settings import SettingsError, ShopSettingsInput
from tests.conftest import CASHIER_PIN, OWNER_PIN, login

VALID_TAX_ID = "0105555000005"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_input(**overrides) -> ShopSettingsInput:
    data = ShopSettingsInput(shop_name="ร้านทดสอบวัสดุ", address="1 ถ.สุขุมวิท", phone="0812345678")
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


# --- tax id / vat rate -----------------------------------------------------


@pytest.mark.parametrize("tax_id", [VALID_TAX_ID, "3100100000006"])
def test_valid_tax_id(tax_id):
    assert svc.is_valid_thai_tax_id(tax_id)


@pytest.mark.parametrize(
    "tax_id", ["0105555000004", "010555500000", "01055550000055", "01055550000a5", ""]
)
def test_invalid_tax_id(tax_id):
    assert not svc.is_valid_thai_tax_id(tax_id)


@pytest.mark.parametrize("text,bp", [("7", 700), ("7.00", 700), (" 7.5 ", 750), ("10.25", 1025)])
def test_parse_vat_rate(text, bp):
    assert svc.parse_vat_rate_bp(text) == bp
    assert isinstance(svc.parse_vat_rate_bp(text), int)


@pytest.mark.parametrize("text", ["", "abc", "7.001", "0", "-7", "100", "Infinity", "NaN"])
def test_parse_vat_rate_invalid(text):
    with pytest.raises(ValueError):
        svc.parse_vat_rate_bp(text)


def test_format_vat_rate():
    assert svc.format_vat_rate(700) == "7.00"
    assert svc.format_vat_rate(1025) == "10.25"


# --- update service --------------------------------------------------------


def test_update_basic_fields(db, owner):
    row = svc.update_shop_settings(db, owner.id, make_input(shop_name="  ร้าน   ใหม่ "))
    db.commit()
    assert row.shop_name == "ร้าน ใหม่"
    assert row.vat_mode == "none"
    assert row.branch_no == "00000"


def test_shop_name_required(db, owner):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(db, owner.id, make_input(shop_name="  "))
    assert "shop_name" in e.value.errors


def test_vat_mode_requires_tax_id(db, owner):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(db, owner.id, make_input(vat_mode="vat"))
    assert "tax_id" in e.value.errors
    assert svc.get_shop_settings(db).vat_mode == "none"


def test_vat_mode_rejects_bad_checksum(db, owner):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(db, owner.id, make_input(vat_mode="vat", tax_id="0105555000004"))
    assert "tax_id" in e.value.errors


def test_enable_vat_mode_head_office(db, owner):
    row = svc.update_shop_settings(
        db, owner.id, make_input(vat_mode="vat", tax_id="0-1055-55000-00-5", vat_rate="7")
    )
    db.commit()
    assert row.vat_mode == "vat"
    assert row.tax_id == VALID_TAX_ID
    assert row.branch_no == "00000"
    assert row.vat_rate_bp == 700


def test_enable_vat_mode_branch(db, owner):
    row = svc.update_shop_settings(
        db,
        owner.id,
        make_input(vat_mode="vat", tax_id=VALID_TAX_ID, branch_type="branch", branch_no="3"),
    )
    assert row.branch_no == "00003"


@pytest.mark.parametrize("branch_no", ["", "0", "00000", "abc", "123456"])
def test_invalid_branch_no(db, owner, branch_no):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(
            db,
            owner.id,
            make_input(vat_mode="vat", tax_id=VALID_TAX_ID, branch_type="branch", branch_no=branch_no),
        )
    assert "branch_no" in e.value.errors


@pytest.mark.parametrize("value", ["0", "-1", "abc", "3651"])
def test_invalid_backup_keep_days(db, owner, value):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(db, owner.id, make_input(backup_keep_days=value))
    assert "backup_keep_days" in e.value.errors


def test_backup_dir_must_be_absolute(db, owner, tmp_path):
    with pytest.raises(SettingsError) as e:
        svc.update_shop_settings(db, owner.id, make_input(backup_dir="backups"))
    assert "backup_dir" in e.value.errors
    row = svc.update_shop_settings(db, owner.id, make_input(backup_dir=str(tmp_path)))
    assert row.backup_dir == str(tmp_path)


def test_update_is_audited_with_changes(db, owner):
    svc.update_shop_settings(db, owner.id, make_input())
    db.commit()
    svc.update_shop_settings(db, owner.id, make_input())  # no changes -> no new log
    db.commit()
    logs = db.scalars(select(AuditLog).where(AuditLog.action == "settings_update")).all()
    assert len(logs) == 1
    assert logs[0].user_id == owner.id
    assert logs[0].detail["changes"]["shop_name"] == ["", "ร้านทดสอบวัสดุ"]


def test_save_logo(db, owner, config):
    row = svc.save_logo(db, owner.id, config.uploads_dir, PNG_BYTES)
    db.commit()
    assert row.logo_filename == "logo.png"
    assert (config.uploads_dir / "logo.png").read_bytes() == PNG_BYTES
    svc.save_logo(db, owner.id, config.uploads_dir, b"\xff\xd8\xff" + b"\x00" * 10)
    assert not (config.uploads_dir / "logo.png").exists()
    assert (config.uploads_dir / "logo.jpg").exists()


@pytest.mark.parametrize(
    "content",
    [b"GIF89a....", b"<svg></svg>", b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024)],
    ids=["gif", "svg", "too-large"],
)
def test_save_logo_rejects(db, owner, config, content):
    with pytest.raises(SettingsError):
        svc.save_logo(db, owner.id, config.uploads_dir, content)


# --- routes ----------------------------------------------------------------


def test_settings_requires_login(client, owner):
    assert client.get("/settings", follow_redirects=False).headers["location"] == "/login"


def test_cashier_cannot_open_settings(client, cashier):
    login(client, cashier, CASHIER_PIN)
    assert client.get("/settings").status_code == 403
    r = client.post("/settings", data={"shop_name": "แฮก"})
    assert r.status_code == 403


def test_owner_saves_settings(client, db, owner):
    login(client, owner, OWNER_PIN)
    assert client.get("/settings").status_code == 200
    r = client.post(
        "/settings",
        data={
            "shop_name": "ร้านวัสดุดี",
            "vat_mode": "vat",
            "tax_id": VALID_TAX_ID,
            "branch_type": "head",
            "vat_rate": "7.00",
            "price_includes_vat": "true",
            "backup_keep_days": "30",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    row = svc.get_shop_settings(db)
    assert (row.shop_name, row.vat_mode, row.price_includes_vat) == ("ร้านวัสดุดี", "vat", True)
    assert row.allow_abbreviated_invoice is False


def test_owner_settings_error_shown(client, owner):
    login(client, owner, OWNER_PIN)
    r = client.post("/settings", data={"shop_name": "ร้าน", "vat_mode": "vat", "backup_keep_days": "30"})
    assert r.status_code == 400
    assert "เลขประจำตัวผู้เสียภาษี" in r.text


def test_logo_upload_and_serve(client, owner):
    login(client, owner, OWNER_PIN)
    assert client.get("/settings/logo").status_code == 404
    r = client.post("/settings/logo", files={"logo": ("x.png", PNG_BYTES, "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/settings/logo")
    assert r.status_code == 200
    assert r.content == PNG_BYTES
