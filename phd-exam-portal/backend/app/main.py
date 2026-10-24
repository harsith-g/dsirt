"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import NotAuthenticatedError
from app.core.redis_client import close_redis, get_redis
from app.db.session import dispose_engine, engine
from app.routers import (
    admin as admin_router,
    auth_admin,
    auth_candidate,
    exam as exam_router,
    pages,
    signaling as signaling_router,
    violations as violations_router,
)
from app.services.signaling import pubsub_listener

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
logger = logging.getLogger("examportal")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.SNAPSHOT_DIR).mkdir(parents=True, exist_ok=True)

    stop = asyncio.Event()
    listener = asyncio.create_task(pubsub_listener(stop))
    app.state.signal_stop = stop
    app.state.signal_task = listener
    logger.info("%s starting (env=%s)", settings.APP_NAME, settings.ENVIRONMENT)

    if settings.ENVIRONMENT == "production" and settings.SECRET_KEY.startswith("CHANGE-ME"):
        logger.error(
            "SECRET_KEY is still the development default. Set SECRET_KEY before serving."
        )
    try:
        yield
    finally:
        stop.set()
        listener.cancel()
        try:
            await listener
        except (asyncio.CancelledError, Exception):
            pass
        await close_redis()
        await dispose_engine()
        logger.info("shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.ENVIRONMENT != "production" else None,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
Path(settings.SNAPSHOT_DIR).mkdir(parents=True, exist_ok=True)
app.mount(
    settings.SNAPSHOT_URL_PREFIX,
    StaticFiles(directory=settings.SNAPSHOT_DIR),
    name="snapshots",
)

app.include_router(auth_candidate.router)
app.include_router(auth_admin.router)
app.include_router(exam_router.router)
app.include_router(violations_router.router)
app.include_router(admin_router.router)
app.include_router(signaling_router.router)
app.include_router(pages.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(self), microphone=(self), geolocation=()"
    )
    if request.url.path.startswith(("/exam", "/api/exam")):
        response.headers.setdefault("Cache-Control", "no-store, max-age=0")
    return response


@app.exception_handler(NotAuthenticatedError)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedError):
    """API callers get 401 JSON; browsers get sent to the right login page."""
    wants_html = "text/html" in (request.headers.get("accept") or "")
    if wants_html and not request.url.path.startswith("/api/"):
        target = "/admin/login" if exc.scope == "admin" else "/login"
        return RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": exc.detail, "scope": exc.scope},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Invalid request.", "errors": exc.errors()},
    )


@app.get("/health", include_in_schema=False)
async def health():
    db_ok = redis_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    try:
        await get_redis().ping()
    except Exception:
        redis_ok = False
    healthy = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "db": db_ok, "redis": redis_ok},
    )
