import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


from backend.database import SessionLocal
from backend.routers import auth, exam, admin, users, questions
from backend.config import UPLOADS_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="PhD Entrance Examination Platform")

# ---------------------------------------------------------------------------
# Schema management: Alembic ONLY. Do not add Base.metadata.create_all() or
# drop_all() here — those are dev-convenience footguns in production (a
# create_all() race between multiple booting Gunicorn workers, or a stray
# drop_all() in the wrong environment, is exactly the kind of thing that
# should be impossible). Provision/upgrade the schema out-of-band with:
#     alembic upgrade head
# before starting the app server (see deploy/phd-exam.service, which runs
# this as ExecStartPre; local dev: run it once after `pip install -r
# requirements.txt`, same command, against your local DATABASE_URL).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CORS: locked down by default. Set ALLOWED_ORIGINS (comma-separated) in the
# environment for any origin that should be allowed to call this API from a
# browser context other than same-origin page loads (this app is normally
# served same-origin via Jinja2 templates, so CORS mainly matters if you ever
# point a separate frontend at this API). Defaults cover the VPS's own IP /
# hostname plus localhost for development.
# ---------------------------------------------------------------------------
_default_origins = [
    "http://localhost:8000", "http://127.0.0.1:8000",
    "http://62.72.29.111", "https://62.72.29.111",
    "http://srv1990910.hstgr.cloud", "https://srv1990910.hstgr.cloud",
]
_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = (
    [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    if _allowed_origins_env else _default_origins
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(exam.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(questions.router)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    return JSONResponse(
        status_code=500,
        content={"detail": "A database error occurred. Please try again later."},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"An unexpected error occurred: {str(exc)}"},
    )


@app.on_event("startup")
def on_startup():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/healthz")
def healthz():
    """
    Liveness/readiness probe for nginx, systemd, or an uptime monitor.
    Verifies the DB pool can actually serve a connection — a hung/exhausted
    Postgres pool is exactly the kind of failure that should flip a load
    balancer/monitor red instead of silently 500-ing every real request.
    """
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "db": "unreachable"})
    return {"status": "ok"}


@app.get("/")
def root():
    return RedirectResponse(url="/login")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


# ------------------------------------------------------------------ #
# Candidate routes
# ------------------------------------------------------------------ #

@app.get("/candidate")
def candidate_dashboard_page(request: Request):
    """
    Candidate landing page after login.
    The JS on this page checks the JWT role and redirects to /login
    if not a candidate. The actual dashboard data is loaded via
    GET /exam/dashboard.
    """
    return templates.TemplateResponse(request=request, name="candidate_dashboard.html")


@app.get("/candidate/exam")
def candidate_exam_page(request: Request):
    """
    The exam interface (Phase 1 and Phase 2).
    Candidates reach this page from the dashboard after clicking
    START PHASE 1 or START PHASE 2.
    """
    return templates.TemplateResponse(request=request, name="exam.html")


# ------------------------------------------------------------------ #
# Admin / setter routes
# ------------------------------------------------------------------ #

@app.get("/exam")
def exam_page_legacy(request: Request):
    """Legacy /exam route — redirects to new candidate exam URL."""
    return RedirectResponse(url="/candidate/exam")


@app.get("/admin")
def admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_dashboard.html")


@app.get("/setter")
def setter_page(request: Request):
    return templates.TemplateResponse(request=request, name="setter_dashboard.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
