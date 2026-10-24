"""Server-rendered pages.

The two login pages live at different URLs with different templates and
different API dependencies, per the strict-separation requirement. An
unauthenticated visitor to a protected page is redirected to the matching
login page rather than shown a JSON 401.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import optional_admin, optional_candidate
from app.db.session import get_db
from app.enums import SessionStatus
from app.models import Admin, Candidate, ExamResult, ExamSession
from app.services import exam_service as svc

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


def _ctx(request: Request, **kwargs) -> dict:
    return {
        "request": request,
        "app_name": settings.APP_NAME,
        # Resolved from disk at startup; see Settings.logo_url.
        "logo_url": settings.logo_url,
        **kwargs,
    }


# ---------------------------------------------------------------- candidate
@router.get("/", response_class=HTMLResponse)
async def root(candidate: Candidate | None = Depends(optional_candidate)):
    return RedirectResponse("/dashboard" if candidate else "/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def candidate_login_page(
    request: Request, candidate: Candidate | None = Depends(optional_candidate)
):
    if candidate:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("candidate/login.html", _ctx(request))


@router.get("/dashboard", response_class=HTMLResponse)
async def candidate_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate | None = Depends(optional_candidate),
):
    if not candidate:
        return RedirectResponse("/login", status_code=302)
    exam = None
    session = None
    try:
        exam = await svc.get_active_exam(db)
        session = await svc.get_or_create_session(db, candidate.id, exam)
        await db.commit()
    except Exception:
        await db.rollback()
    return templates.TemplateResponse(
        "candidate/dashboard.html",
        _ctx(request, candidate=candidate, exam=exam, session=session),
    )


@router.get("/exam", response_class=HTMLResponse)
async def exam_page(
    request: Request, candidate: Candidate | None = Depends(optional_candidate)
):
    if not candidate:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "candidate/exam.html",
        _ctx(request, candidate=candidate, ice_servers=settings.ice_servers),
    )


@router.get("/domain-selection", response_class=HTMLResponse)
async def domain_selection_page(
    request: Request, candidate: Candidate | None = Depends(optional_candidate)
):
    if not candidate:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "candidate/domain_selection.html", _ctx(request, candidate=candidate)
    )


@router.get("/completed", response_class=HTMLResponse)
async def completed_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate | None = Depends(optional_candidate),
):
    if not candidate:
        return RedirectResponse("/login", status_code=302)
    result = None
    session = (
        await db.execute(
            select(ExamSession)
            .where(ExamSession.candidate_id == candidate.id)
            .order_by(ExamSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session:
        row = (
            await db.execute(select(ExamResult).where(ExamResult.session_id == session.id))
        ).scalar_one_or_none()
        if row and row.is_published:
            result = row
    return templates.TemplateResponse(
        "candidate/completed.html",
        _ctx(request, candidate=candidate, session=session, result=result),
    )


# -------------------------------------------------------------------- admin
@router.get("/admin", response_class=HTMLResponse)
async def admin_root(admin: Admin | None = Depends(optional_admin)):
    return RedirectResponse("/admin/dashboard" if admin else "/admin/login", status_code=302)


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, admin: Admin | None = Depends(optional_admin)):
    if admin:
        return RedirectResponse("/admin/dashboard", status_code=302)
    return templates.TemplateResponse("admin/login.html", _ctx(request))


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard_page(
    request: Request, admin: Admin | None = Depends(optional_admin)
):
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin/dashboard.html", _ctx(request, admin=admin))


@router.get("/admin/proctoring", response_class=HTMLResponse)
async def admin_proctoring_page(
    request: Request, admin: Admin | None = Depends(optional_admin)
):
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse(
        "admin/proctoring.html", _ctx(request, admin=admin, ice_servers=settings.ice_servers)
    )


@router.get("/admin/questions", response_class=HTMLResponse)
async def admin_questions_page(
    request: Request, admin: Admin | None = Depends(optional_admin)
):
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin/questions.html", _ctx(request, admin=admin))


@router.get("/admin/candidates", response_class=HTMLResponse)
async def admin_candidates_page(
    request: Request, admin: Admin | None = Depends(optional_admin)
):
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin/candidates.html", _ctx(request, admin=admin))
