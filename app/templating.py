from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import APP_DIR
from app.services.money import format_money, format_money_input, format_qty, format_qty_input

templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["app_version"] = __version__
templates.env.filters["money"] = format_money
templates.env.filters["money_input"] = format_money_input
templates.env.filters["qty"] = format_qty
templates.env.filters["qty_input"] = format_qty_input
