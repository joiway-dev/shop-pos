"""Filesystem locations and process-level settings.

Shop-level settings (name, VAT mode, ...) live in the `shop_settings` table,
see `app.services.settings`. This module only knows where data is stored.

The data directory can be moved with the SHOP_DATA_DIR environment variable so
program files and shop data stay separate (program upgrades never touch data).
"""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    auto_backup: bool = True  # daily backup task (tests turn it off)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shop.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def default_backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def secret_key(self) -> str:
        """Session signing key, generated once per installation."""
        self.ensure_dirs()
        path = self.secret_key_path
        if not path.exists():
            path.write_text(secrets.token_hex(32), encoding="ascii")
        return path.read_text(encoding="ascii").strip()


def load_config() -> AppConfig:
    data_dir = os.environ.get("SHOP_DATA_DIR")
    return AppConfig(data_dir=Path(data_dir) if data_dir else BASE_DIR / "data")
