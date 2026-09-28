from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .db import get_engine, get_sessionmaker
from .routers import admin, patient, staff
from .services import expire_stale_intakes

log = logging.getLogger("intake")

_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self' https://*.amazoncognito.com https://cognito-idp.*.amazonaws.com; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self' https://*.amazoncognito.com; object-src 'none'"
)


async def _sweeper() -> None:
    """Every 15 minutes: expire old links. Once a day: retention purge."""
    from .retention import run_retention

    last_purge = 0.0
    while True:
        try:
            with get_sessionmaker()() as db:
                await asyncio.to_thread(expire_stale_intakes, db)
                if time.monotonic() - last_purge > 24 * 3600 or last_purge == 0.0:
                    await asyncio.to_thread(run_retention, db)
                    last_purge = time.monotonic()
        except Exception:  # keep sweeping even if one pass fails
            log.exception("background sweep failed")
        await asyncio.sleep(15 * 60)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from .keys import check_keyfile_before_start

    check_keyfile_before_start()
    task = asyncio.create_task(_sweeper()) if get_settings().environment != "test" else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Dental Patient Intake",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/api/openapi.json",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = request.headers.get("x-amzn-trace-id", "")[:64] or uuid.uuid4().hex
        response = await call_next(request)
        h = response.headers
        h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        h["Content-Security-Policy"] = _CSP
        h["X-Request-Id"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
            h["Pragma"] = "no-cache"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never echo internals (which could include PHI) back to the client.
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", ""))
        return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})

    @app.get("/api/health")
    def health():
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
        return {"ok": True}

    @app.get("/api/config")
    def public_config():
        """Non-secret values the SPA needs to start the Cognito sign-in flow."""
        return {
            "auth_mode": settings.auth_mode,
            "deployment": settings.deployment,
            "cognito_domain": settings.cognito_domain,
            "cognito_client_id": settings.cognito_client_id,
        }

    app.include_router(patient.router)
    app.include_router(staff.router)
    app.include_router(admin.router)
    if settings.auth_mode == "local":
        from .routers import auth_local

        app.include_router(auth_local.router)
    if settings.auth_mode == "dev":
        from .routers import dev

        app.include_router(dev.router)

    static = Path(settings.static_dir)
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
        index = static / "index.html"

        @app.exception_handler(StarletteHTTPException)
        async def spa_fallback(request: Request, exc: StarletteHTTPException):
            if exc.status_code == 404 and not request.url.path.startswith("/api/") and request.method == "GET":
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                                headers=getattr(exc, "headers", None))

    return app


app = create_app()
