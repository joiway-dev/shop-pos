import os
import tempfile
from pathlib import Path

# Importing app.main builds the module-level app; point it at a throwaway
# directory so tests never touch the real data/ folder.
os.environ["SHOP_DATA_DIR"] = tempfile.mkdtemp(prefix="shop-test-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import BASE_DIR, AppConfig  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.user import ROLE_CASHIER, ROLE_OWNER  # noqa: E402
from app.services import auth  # noqa: E402

OWNER_PIN = "1234"
CASHIER_PIN = "5678"


def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    cfg = AppConfig(data_dir=tmp_path / "data", auto_backup=False)
    cfg.ensure_dirs()
    command.upgrade(alembic_config(cfg.db_url), "head")
    return cfg


@pytest.fixture
def app(config):
    application = create_app(config)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def db(app):
    session = app.state.session_factory()
    yield session
    session.close()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner(db):
    user = auth.create_first_owner(db, "เจ้าของ", OWNER_PIN)
    db.commit()
    return user


@pytest.fixture
def cashier(db, owner):
    user = auth.create_user(db, owner.id, "พนักงาน", ROLE_CASHIER, CASHIER_PIN)
    db.commit()
    return user


def login(client: TestClient, user, pin: str):
    return client.post("/login", data={"user_id": user.id, "pin": pin}, follow_redirects=False)


__all__ = ["OWNER_PIN", "CASHIER_PIN", "ROLE_OWNER", "login"]
