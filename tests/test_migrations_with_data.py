"""Upgrading a shop database that already has data (not just an empty one).

Regression: migration 0006 rebuilds the sales table; with foreign keys on this
failed as soon as sale_lines pointed at existing sales.
"""

import sqlite3

from alembic import command

from app.config import AppConfig
from tests.conftest import alembic_config


def test_upgrade_keeps_existing_sales(tmp_path):
    cfg = AppConfig(data_dir=tmp_path / "data")
    cfg.ensure_dirs()
    alembic = alembic_config(cfg.db_url)
    command.upgrade(alembic, "0005")

    con = sqlite3.connect(cfg.db_path)
    now = "2026-09-28 10:00:00"
    con.execute("INSERT INTO users (id, created_at, updated_at, name, role, pin_hash, is_active, failed_pin_count,"
                " can_receive_stock, can_see_cost) VALUES (1, ?, ?, 'o', 'owner', 'x', 1, 0, 0, 0)", (now, now))
    con.execute("INSERT INTO products (id, created_at, updated_at, sku, name, name_normalized, base_unit, avg_cost,"
                " vat_exempt, is_active, min_stock_qty) VALUES (1, ?, ?, 'P1', 'ปูน', 'ปูน', 'ถุง', 0, 0, 1, 0)", (now, now))
    con.execute("INSERT INTO sales (id, created_at, updated_at, doc_type, doc_no, status, subtotal, discount,"
                " vatable_amount, exempt_amount, vat_amount, total, payment_type, shop_snapshot, vat_rate_snapshot,"
                " print_count, user_id) VALUES (1, ?, ?, 'RC', 'RC1', 'completed', 100, 0, 0, 0, 0, 100, 'cash', '{}',"
                " 0, 0, 1)", (now, now))
    con.execute("INSERT INTO sale_lines (id, created_at, updated_at, sale_id, product_id, sku_snapshot,"
                " product_name_snapshot, unit_name, factor_to_base, qty, unit_price, discount, line_total, vat_exempt,"
                " unit_cost_snapshot) VALUES (1, ?, ?, 1, 1, 'P1', 'ปูน', 'ถุง', 1000, 1000, 100, 0, 100, 0, 0)", (now, now))
    con.commit()
    con.close()

    command.upgrade(alembic, "head")

    con = sqlite3.connect(cfg.db_path)
    assert con.execute("SELECT doc_no, customer_id, due_date FROM sales").fetchall() == [("RC1", None, None)]
    assert con.execute("SELECT count(*) FROM sale_lines").fetchone() == (1,)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    fks = {row[2] for row in con.execute("PRAGMA foreign_key_list(sales)")}
    assert "customers" in fks
    con.close()
