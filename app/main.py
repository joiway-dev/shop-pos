from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import APP_DIR, AppConfig, load_config
from app.db import create_db_engine, create_session_factory
from app.routes import auth, settings
from app.routes.deps import LoginRequired, OwnerRequired
from app.templating import templates


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    config.ensure_dirs()

    app = FastAPI(title="ระบบร้านวัสดุก่อสร้าง", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.engine = create_db_engine(config.db_url)
    app.state.session_factory = create_session_factory(app.state.engine)

    # Session cookie lives until the browser closes. SameSite=lax blocks
    # cross-site form posts from carrying the cookie.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key(),
        session_cookie="shop_session",
        max_age=None,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired) -> Response:
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(OwnerRequired)
    async def _owner_required(request: Request, _exc: OwnerRequired) -> Response:
        return templates.TemplateResponse(request, "forbidden.html", {}, status_code=403)

    app.include_router(auth.router)
    app.include_router(settings.router)
    return app


app = create_app()
