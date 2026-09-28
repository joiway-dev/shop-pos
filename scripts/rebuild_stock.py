"""Rebuild the stock balance cache from stock_movements (CLAUDE.md rule 5).

Usage: scripts\\rebuild_stock.bat  (safe to run any time; close the POS first)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.db import create_db_engine, create_session_factory  # noqa: E402
from app.services import stock  # noqa: E402
from app.services.audit import log_action  # noqa: E402


def main() -> int:
    config = load_config()
    if not config.db_path.exists():
        print("ไม่พบฐานข้อมูล:", config.db_path)
        return 1
    with create_session_factory(create_db_engine(config.db_url))() as db:
        count = stock.rebuild_balances(db)
        log_action(db, None, "stock_rebuild", "stock_balances", None, {"products": count})
        db.commit()
    print(f"คำนวณยอดสต็อกใหม่เรียบร้อย ({count} สินค้า)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
