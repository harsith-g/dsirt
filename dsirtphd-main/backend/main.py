from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError


from backend.database import Base, engine
from backend.routers import auth, exam, admin, users, questions
from backend.config import UPLOADS_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="PhD Entrance Examination Platform")

Base.metadata.create_all(bind=engine)

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
