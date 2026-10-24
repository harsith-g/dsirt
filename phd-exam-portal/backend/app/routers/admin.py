"""Admin API -- `/api/admin/*`. Every route is behind an admin-role guard."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import client_ip, require_admin, require_proctor, require_super_admin
from app.core.redis_client import get_redis, list_active_sessions
from app.db.session import get_db
from app.enums import (
    ActorType,
    CandidateStatus,
    Phase,
    SessionStatus,
)
from app.models import (
    Admin,
    Candidate,
    Domain,
    Exam,
    ExamResult,
    ExamSession,
    Question,
    Violation,
)
from app.schemas import (
    AdminDashboardOut,
    CandidateOut,
    CandidateStatusUpdate,
    DomainOut,
    ImportResultOut,
    LiveSessionOut,
    MessageOut,
    ViolationOut,
)
from app.services import exam_service as svc
from app.services.audit import log_action
from app.services.question_importer import build_template_csv, import_bytes

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# --------------------------------------------------------------- dashboard
@router.get("/dashboard", response_model=AdminDashboardOut)
async def dashboard(
    db: AsyncSession = Depends(get_db), admin: Admin = Depends(require_proctor)
):
    async def count(stmt) -> int:
        return int((await db.execute(stmt)).scalar_one())

    since = datetime.now(timezone.utc) - timedelta(days=1)
    redis_ok = True
    try:
        await get_redis().ping()
    except Exception:
        redis_ok = False

    live = await list_active_sessions() if redis_ok else []

    return AdminDashboardOut(
        candidates_total=await count(select(func.count(Candidate.id))),
        candidates_approved=await count(
            select(func.count(Candidate.id)).where(
                Candidate.status.in_([CandidateStatus.APPROVED, CandidateStatus.ACTIVE])
            )
        ),
        sessions_in_progress=await count(
            select(func.count(ExamSession.id)).where(
                ExamSession.status.in_(
                    [SessionStatus.PHASE_1_IN_PROGRESS, SessionStatus.PHASE_2_IN_PROGRESS]
                )
            )
        ),
        sessions_completed=await count(
            select(func.count(ExamSession.id)).where(
                ExamSession.status == SessionStatus.COMPLETED
            )
        ),
        questions_phase1=await count(
            select(func.count(Question.id)).where(
                Question.phase == Phase.PHASE_1, Question.is_active.is_(True)
            )
        ),
        questions_phase2=await count(
            select(func.count(Question.id)).where(
                Question.phase == Phase.PHASE_2, Question.is_active.is_(True)
            )
        ),
        violations_today=await count(
            select(func.count(Violation.id)).where(Violation.timestamp >= since)
        ),
        online_now=len(live),
        db_ok=True,
        redis_ok=redis_ok,
    )


# ------------------------------------------------------------ live sessions
@router.get("/sessions/live", response_model=list[LiveSessionOut])
async def live_sessions(
    db: AsyncSession = Depends(get_db), admin: Admin = Depends(require_proctor)
):
    """Every in-progress session, annotated with Redis presence."""
    rows = (
        (
            await db.execute(
                select(ExamSession)
                .options(
                    selectinload(ExamSession.candidate),
                    selectinload(ExamSession.selected_domain),
                )
                .where(
                    ExamSession.status.in_(
                        [
                            SessionStatus.PHASE_1_IN_PROGRESS,
                            SessionStatus.PHASE_1_COMPLETED,
                            SessionStatus.PHASE_2_IN_PROGRESS,
                        ]
                    )
                )
                .order_by(ExamSession.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    presence = {h["session_id"]: h for h in await list_active_sessions()}

    out: list[LiveSessionOut] = []
    for s in rows:
        hb = presence.get(str(s.id), {})
        ping = hb.get("ping_ms") or ""
        last_seen = hb.get("last_seen")
        out.append(
            LiveSessionOut(
                session_id=s.id,
                candidate_id=s.candidate_id,
                registration_number=s.candidate.registration_number,
                full_name=s.candidate.full_name,
                status=s.status,
                current_phase=s.current_phase,
                domain=s.selected_domain.name if s.selected_domain else None,
                seconds_left=svc.time_status(s).seconds_left,
                violation_count=s.violation_count,
                ping_ms=int(ping) if str(ping).isdigit() else None,
                camera_active=hb.get("camera_active", "1") == "1",
                online=bool(hb),
                last_seen=datetime.fromisoformat(last_seen) if last_seen else None,
            )
        )
    return out


@router.get("/sessions/{session_id}/violations", response_model=list[ViolationOut])
async def session_violations(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_proctor),
):
    rows = (
        (
            await db.execute(
                select(Violation)
                .where(Violation.session_id == session_id)
                .order_by(Violation.timestamp.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# --------------------------------------------------------------- candidates
@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(
    q: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    stmt = select(Candidate).order_by(Candidate.registration_number).limit(min(limit, 1000))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Candidate.full_name).like(like)
            | func.lower(Candidate.email).like(like)
            | func.lower(Candidate.registration_number).like(like)
        )
    return list((await db.execute(stmt)).scalars().all())


@router.patch("/candidates/{candidate_id}/status", response_model=CandidateOut)
async def update_candidate_status(
    candidate_id: uuid.UUID,
    payload: CandidateStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    previous = candidate.status
    candidate.status = payload.status
    await log_action(
        db,
        actor_type=ActorType.ADMIN,
        action="candidate.status.changed",
        actor_id=admin.id,
        actor_label=admin.email,
        target_type="candidate",
        target_id=str(candidate_id),
        payload={
            "from": previous.value,
            "to": payload.status.value,
            "reason": payload.reason,
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(candidate)
    return candidate


# ------------------------------------------------------------ question bank
@router.get("/questions/template")
async def question_template(admin: Admin = Depends(require_admin)):
    return Response(
        content=build_template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="question_import_template.csv"'},
    )


@router.post("/questions/import", response_model=ImportResultOut)
async def import_questions(
    request: Request,
    file: UploadFile = File(...),
    # Used only for the compact layout, which carries no placement columns.
    # A strict-schema file ignores these and takes phase/domain from its rows.
    phase: str | None = Form(default=None),
    domain_code: str | None = Form(default=None),
    marks: float = Form(default=1.0),
    negative_marks: float = Form(default=0.0),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    if marks <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Marks must be greater than zero."
        )
    if negative_marks < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Negative marks cannot be below zero."
        )

    report = await import_bytes(
        db,
        data,
        phase=(phase or None),
        domain_code=(domain_code or None),
        marks=marks,
        negative_marks=negative_marks,
    )
    if report.ok:
        await log_action(
            db,
            actor_type=ActorType.ADMIN,
            action="questions.imported",
            actor_id=admin.id,
            actor_label=admin.email,
            target_type="file",
            target_id=file.filename,
            payload={"imported": report.imported, "skipped": report.duplicates_in_db},
            ip_address=client_ip(request),
        )
        await db.commit()
    else:
        # Pass 1 failed: nothing was written, and nothing is committed.
        await db.rollback()
    return ImportResultOut(**report.as_dict())


@router.get("/domains", response_model=list[DomainOut])
async def list_domains(
    db: AsyncSession = Depends(get_db), admin: Admin = Depends(require_proctor)
):
    rows = (
        await db.execute(
            select(Domain, func.count(Question.id))
            .join(
                Question,
                (Question.domain_id == Domain.id) & (Question.is_active.is_(True)),
                isouter=True,
            )
            .group_by(Domain.id)
            .order_by(Domain.name)
        )
    ).all()
    return [
        DomainOut(
            id=d.id,
            code=d.code,
            name=d.name,
            description=d.description,
            question_count=int(c),
        )
        for d, c in rows
    ]


# ------------------------------------------------------------------ results
@router.post("/results/publish", response_model=MessageOut)
async def publish_results(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_super_admin),
):
    now = datetime.now(timezone.utc)
    results = (
        (
            await db.execute(
                select(ExamResult)
                .join(ExamSession, ExamSession.id == ExamResult.session_id)
                .where(
                    ExamResult.is_published.is_(False),
                    ExamSession.status == SessionStatus.COMPLETED,
                )
            )
        )
        .scalars()
        .all()
    )
    for r in results:
        r.is_published = True
        r.published_at = now
    await log_action(
        db,
        actor_type=ActorType.ADMIN,
        action="results.published",
        actor_id=admin.id,
        actor_label=admin.email,
        payload={"count": len(results)},
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageOut(message=f"Published {len(results)} result(s).")


@router.get("/results/export")
async def export_results(
    db: AsyncSession = Depends(get_db), admin: Admin = Depends(require_admin)
):
    import csv
    import io

    rows = (
        await db.execute(
            select(ExamResult, ExamSession, Candidate, Domain)
            .join(ExamSession, ExamSession.id == ExamResult.session_id)
            .join(Candidate, Candidate.id == ExamSession.candidate_id)
            .join(Domain, Domain.id == ExamSession.selected_domain_id, isouter=True)
            .order_by(ExamResult.total_score.desc())
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "registration_number", "full_name", "email", "domain", "status",
            "phase1_score", "phase1_max", "phase1_correct", "phase1_attempted",
            "phase2_score", "phase2_max", "phase2_correct", "phase2_attempted",
            "total_score", "total_max", "violations", "published",
        ]
    )
    for result, session, candidate, domain in rows:
        writer.writerow(
            [
                candidate.registration_number, candidate.full_name, candidate.email,
                domain.name if domain else "", session.status.value,
                result.phase1_score, result.phase1_max, result.phase1_correct,
                result.phase1_attempted, result.phase2_score, result.phase2_max,
                result.phase2_correct, result.phase2_attempted, result.total_score,
                result.total_max, session.violation_count, result.is_published,
            ]
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="results-{stamp}.csv"'},
    )


# --------------------------------------------------------------------- exam
@router.post("/exam/{exam_id}/toggle", response_model=MessageOut)
async def toggle_exam(
    exam_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_super_admin),
):
    exam = await db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found.")
    exam.is_active = not exam.is_active
    await log_action(
        db,
        actor_type=ActorType.ADMIN,
        action="exam.toggled",
        actor_id=admin.id,
        actor_label=admin.email,
        target_type="exam",
        target_id=str(exam_id),
        payload={"is_active": exam.is_active},
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageOut(message=f"Exam is now {'active' if exam.is_active else 'inactive'}.")
