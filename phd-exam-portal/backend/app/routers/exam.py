"""Candidate-facing exam API -- `/api/exam/*` and `/api/sessions/*`.

Every endpoint calls `enforce_deadline` first, so a phase whose server-side
hard stop has passed is auto-submitted before the request is serviced. That is
what makes the browser timer purely cosmetic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import api_rate_limiter, client_ip, get_current_candidate
from app.core.redis_client import record_heartbeat
from app.db.session import get_db
from app.enums import ActorType, Phase, SessionStatus
from app.models import Candidate, CandidateAnswer, Domain, ExamResult, ExamSession
from app.schemas import (
    AnswerSaveRequest,
    AnswerSaveResponse,
    DomainOut,
    DomainSelectRequest,
    HeartbeatRequest,
    MessageOut,
    PaperOut,
    ResultOut,
    SessionStateOut,
    SubmitResponse,
)
from app.services import exam_service as svc
from app.services.audit import log_action

router = APIRouter(prefix="/api", tags=["exam"], dependencies=[Depends(api_rate_limiter)])


async def _load_session(
    db: AsyncSession, candidate: Candidate, request: Request
) -> tuple[ExamSession, "object"]:
    exam = await svc.get_active_exam(db)
    session = await svc.get_or_create_session(
        db,
        candidate.id,
        exam,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await svc.enforce_deadline(db, session)
    return session, exam


async def _require_own_session(
    db: AsyncSession, candidate: Candidate, session_id: uuid.UUID
) -> ExamSession:
    session = await db.get(ExamSession, session_id)
    if session is None or session.candidate_id != candidate.id:
        # 404 rather than 403 so one candidate cannot probe for another's ids.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session


async def _state(db: AsyncSession, session: ExamSession) -> SessionStateOut:
    ts = svc.time_status(session)
    order = session.question_order.get(session.current_phase.value, [])
    answers = (
        (
            await db.execute(
                select(CandidateAnswer).where(
                    CandidateAnswer.session_id == session.id,
                    CandidateAnswer.phase == session.current_phase,
                )
            )
        )
        .scalars()
        .all()
    )
    answered = sum(
        1 for a in answers if a.selected_option_ids or (a.text_answer or "").strip()
    )
    domain = None
    if session.selected_domain_id:
        d = await db.get(Domain, session.selected_domain_id)
        if d:
            domain = DomainOut(id=d.id, code=d.code, name=d.name, description=d.description)
    return SessionStateOut(
        session_id=session.id,
        status=session.status,
        current_phase=session.current_phase,
        selected_domain=domain,
        domain_locked=session.selected_domain_id is not None,
        seconds_left=ts.seconds_left,
        hard_stop_time=ts.hard_stop,
        server_time=datetime.now(timezone.utc),
        violation_count=session.violation_count,
        total_questions=len(order),
        answered=answered,
        marked_for_review=sum(1 for a in answers if a.is_marked_for_review),
    )


# ------------------------------------------------------------------- state
@router.get("/exam/state", response_model=SessionStateOut)
async def exam_state(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    state = await _state(db, session)
    await db.commit()
    return state


@router.get("/exam/domains", response_model=list[DomainOut])
async def selectable_domains(
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    rows = await svc.list_selectable_domains(db)
    return [
        DomainOut(
            id=d.id, code=d.code, name=d.name, description=d.description, question_count=count
        )
        for d, count in rows
    ]


# ----------------------------------------------------------------- phase 1
@router.post("/exam/phase1/start", response_model=SessionStateOut)
async def start_phase1(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, exam = await _load_session(db, candidate, request)
    await svc.start_phase1(db, session, exam)
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="exam.phase1.start",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="exam_session",
        target_id=str(session.id),
        ip_address=client_ip(request),
    )
    state = await _state(db, session)
    await db.commit()
    return state


@router.post("/exam/phase1/submit", response_model=SubmitResponse)
async def submit_phase1(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    await svc.submit_phase1(db, session)
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="exam.phase1.submit",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="exam_session",
        target_id=str(session.id),
        ip_address=client_ip(request),
    )
    await db.commit()
    return SubmitResponse(
        status=session.status,
        next_step="/domain-selection",
        message="Phase 1 submitted and locked. Choose your Phase 2 domain next.",
    )


# ------------------------------------------------------------ domain lock
@router.post("/exam/domain", response_model=SessionStateOut)
async def choose_domain(
    payload: DomainSelectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    await svc.select_domain(db, session, payload.domain_id)
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="exam.domain.locked",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="domain",
        target_id=str(payload.domain_id),
        ip_address=client_ip(request),
    )
    state = await _state(db, session)
    await db.commit()
    return state


# ----------------------------------------------------------------- phase 2
@router.post("/exam/phase2/start", response_model=SessionStateOut)
async def start_phase2(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, exam = await _load_session(db, candidate, request)
    await svc.start_phase2(db, session, exam)
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="exam.phase2.start",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="exam_session",
        target_id=str(session.id),
        ip_address=client_ip(request),
    )
    state = await _state(db, session)
    await db.commit()
    return state


@router.post("/exam/phase2/submit", response_model=SubmitResponse)
async def submit_phase2(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    await svc.submit_phase2(db, session)
    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action="exam.phase2.submit",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="exam_session",
        target_id=str(session.id),
        ip_address=client_ip(request),
    )
    await db.commit()
    return SubmitResponse(
        status=session.status,
        next_step="/completed",
        message="Examination submitted. You may now close the window.",
    )


# ------------------------------------------------------------------- paper
@router.get("/exam/paper", response_model=PaperOut)
async def get_paper(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    if session.status not in (
        SessionStatus.PHASE_1_IN_PROGRESS,
        SessionStatus.PHASE_2_IN_PROGRESS,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No paper is currently in progress."
        )
    paper = await svc.build_paper(db, session)
    await db.commit()
    return paper


# ---------------------------------------------------------------- autosave
@router.post("/sessions/{session_id}/answer", response_model=AnswerSaveResponse)
async def save_answer(
    session_id: uuid.UUID,
    payload: AnswerSaveRequest,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    """Debounced autosave target. Idempotent server-side upsert."""
    session = await _require_own_session(db, candidate, session_id)
    await svc.enforce_deadline(db, session)
    await svc.save_answer(
        db,
        session,
        payload.question_id,
        selected_option_ids=payload.selected_option_ids,
        text_answer=payload.text_answer,
        is_marked_for_review=payload.is_marked_for_review,
    )
    ts = svc.time_status(session)
    await db.commit()
    return AnswerSaveResponse(
        question_id=payload.question_id,
        saved_at=datetime.now(timezone.utc),
        seconds_left=ts.seconds_left,
        status=session.status,
    )


@router.post("/sessions/{session_id}/heartbeat", response_model=SessionStateOut)
async def heartbeat(
    session_id: uuid.UUID,
    payload: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session = await _require_own_session(db, candidate, session_id)
    await svc.enforce_deadline(db, session)
    ts = svc.time_status(session)
    await record_heartbeat(
        str(session.id),
        {
            "candidate_id": str(candidate.id),
            "registration_number": candidate.registration_number,
            "full_name": candidate.full_name,
            "status": session.status.value,
            "phase": session.current_phase.value,
            "seconds_left": ts.seconds_left,
            "violation_count": session.violation_count,
            "ping_ms": payload.ping_ms if payload.ping_ms is not None else "",
            "camera_active": int(payload.camera_active),
            "fullscreen": int(payload.fullscreen),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        },
    )
    state = await _state(db, session)
    await db.commit()
    return state


# ------------------------------------------------------------------ result
@router.get("/exam/result", response_model=ResultOut)
async def my_result(
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session, _ = await _load_session(db, candidate, request)
    result = (
        await db.execute(select(ExamResult).where(ExamResult.session_id == session.id))
    ).scalar_one_or_none()
    await db.commit()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No result recorded yet."
        )
    if not result.is_published:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Results have not been published yet.",
        )
    return ResultOut.model_validate(result, from_attributes=True)
