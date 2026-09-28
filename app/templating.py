from datetime import date

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import APP_DIR
from app.models import ShopSetting
from app.services.money import format_money, format_money_input, format_qty, format_qty_input

THAI_MONTHS = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)


def thai_date(d: date) -> str:
    """date(2026, 9, 24) -> '24 กันยายน 2569'"""
    return f"{d.day} {THAI_MONTHS[d.month - 1]} {d.year + 543}"


def _layout_context(request: Request) -> dict:
    """Shop name / logo for the sidebar on every page."""
    shop_name, has_logo = "", False
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        with factory() as db:
            row = db.get(ShopSetting, 1)
            if row is not None:
                shop_name, has_logo = row.shop_name, bool(row.logo_filename)
    return {
        "shop_name": shop_name or "ร้านของเรา",
        "shop_has_logo": has_logo,
        "today_text": thai_date(date.today()),
        "current_path": request.url.path,
    }


templates = Jinja2Templates(directory=APP_DIR / "templates", context_processors=[_layout_context])
templates.env.globals["app_version"] = __version__
templates.env.filters["money"] = format_money
templates.env.filters["money_input"] = format_money_input
templates.env.filters["qty"] = format_qty
templates.env.filters["qty_input"] = format_qty_input
