"""Reset an owner's PIN from the shop computer (for a forgotten PIN).

Usage: scripts\\reset_owner_pin.bat  (or: .venv\\Scripts\\python scripts\\reset_owner_pin.py)
Requires physical access to the machine, so no PIN is asked.
"""

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import load_config  # noqa: E402
from app.db import create_db_engine, create_session_factory  # noqa: E402
from app.models import User  # noqa: E402
from app.models.user import ROLE_OWNER  # noqa: E402
from app.services import auth  # noqa: E402


def main() -> int:
    config = load_config()
    if not config.db_path.exists():
        print("ไม่พบฐานข้อมูล:", config.db_path)
        return 1
    session_factory = create_session_factory(create_db_engine(config.db_url))
    with session_factory() as db:
        owners = list(db.scalars(select(User).where(User.role == ROLE_OWNER).order_by(User.id)))
        if not owners:
            print("ยังไม่มีบัญชีเจ้าของร้าน — เปิดระบบเพื่อตั้งค่าครั้งแรก")
            return 1

        print("บัญชีเจ้าของร้าน:")
        for i, owner in enumerate(owners, 1):
            status = "" if owner.is_active else " (ปิดใช้งาน)"
            print(f"  {i}. {owner.name}{status}")
        choice = input("เลือกหมายเลข: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(owners):
            print("ยกเลิก")
            return 1
        owner = owners[int(choice) - 1]

        pin = getpass("PIN ใหม่ (ตัวเลข 4–6 หลัก): ")
        if pin != getpass("ยืนยัน PIN ใหม่: "):
            print("PIN ไม่ตรงกัน — ยกเลิก")
            return 1
        try:
            owner.is_active = True
            auth.reset_pin(db, None, owner, pin, via="reset_owner_pin script")
        except auth.AuthError as e:
            print(e)
            return 1
        db.commit()
        print(f"เปลี่ยน PIN ของ {owner.name} เรียบร้อยแล้ว")
    return 0


if __name__ == "__main__":
    sys.exit(main())
