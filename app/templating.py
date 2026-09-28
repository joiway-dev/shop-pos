from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import APP_DIR

templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["app_version"] = __version__
