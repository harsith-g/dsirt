import asyncio
import json
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from pathlib import Path

from backend import models, schemas, exam_engine
from backend.database import get_db
from backend.security import get_current_candidate, get_current_user
from backend.config import (
    MAX_STRIKES, EXAM_PAPER1_COUNT, VALID_VIOLATION_TYPES,
    DOMAIN_FILENAME_KEYWORDS, normalise_domain,
)
from backend.websocket_manager import manager
from backend.services.ingestion import parse_docx_questions

router = APIRouter(prefix="/exam", tags=["exam"])

# How long the candidate WS will block on a single receive before waking up
# to re-check the session is still in_progress (see candidate_websocket).
CANDIDATE_WS_IDLE_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Legacy fallback: lazily parse a domain's .docx question paper the first
# time a candidate selects that domain, IF the question bank doesn't
# already have enough rows for it (e.g. because nobody has run the ZIP/CSV
# bulk import yet — see services/ingestion.py, the primary path). Domain ->
# filename matching is shared with the ingestion module via config.py so
# the two never disagree about what a given filename means.
# ---------------------------------------------------------------------------
FILES_QUESTION_DIR = Path(__file__).resolve().parent.parent.parent / "files_questiond"


def _find_domain_file(domain_key: str) -> Path | None:
    keywords = DOMAIN_FILENAME_KEYWORDS.get(domain_key, [])
    if not keywords or not FILES_QUESTION_DIR.exists():
        return None
    for entry in sorted(FILES_QUESTION_DIR.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in (".docx", ".xlsx", ".doc"):
            continue
        if any(kw in entry.name.lower() for kw in keywords):
            return entry
    return None


def _ensure_domain_questions_loaded(db: Session, domain: str) -> int:
    from backend.config import EXAM_PAPER2_COUNT
    canonical = normalise_domain(domain)
    existing = db.query(models.Question).filter(models.Question.domain == canonical).count()
    if existing >= EXAM_PAPER2_COUNT:
        return existing
    if canonical not in DOMAIN_FILENAME_KEYWORDS:
        return existing
    file_path = _find_domain_file(canonical)
    if not file_path:
        return existing
    try:
        file_bytes = file_path.read_bytes()
        questions = parse_docx_questions(file_bytes, canonical, phase=2)
        for q in questions:
            db.add(q)
        db.commit()
        return len(questions)
    except Exception as exc:
        db.rollback()
        raise ValueError(f"Failed to load questions for '{canonical}' from '{file_path.name}': {exc}")


def _require_active_session(db: Session, user: models.User) -> models.ExamSession:
    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session is {session.status.value}")
    return session


async def _broadcast_candidate_update(db: Session, session: models.ExamSession, user: models.User):
    nav = exam_engine.build_nav(db, session)
    answered = sum(1 for n in nav if n["answered"])
    await manager.broadcast({
        "type": "candidate_update",
        "session_id": session.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "status": session.status.value,
        "strikes": session.strikes,
        "max_strikes": MAX_STRIKES,
        "answered_count": answered,
        "total_questions": len(session.question_order),
        "time_remaining_seconds": exam_engine.time_remaining_seconds(session),
        "domain_selected": session.domain_selected,
        "paper2_domain": session.paper2_domain,
        "current_phase": session.current_phase,
        "phase1_submitted_at": session.phase1_submitted_at.isoformat() if session.phase1_submitted_at else None,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
    })


# ============================================================
# Candidate Dashboard
# ============================================================

@router.get("/dashboard", response_model=schemas.CandidateDashboardOut)
def get_candidate_dashboard(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    """
    Returns candidate dashboard state. Safe for the candidate to read —
    contains NO scores, NO correct answers, NO result data.
    """
    global_state = exam_engine.get_global_exam_state(db)
    session = exam_engine.get_any_session(db, user)

    if session and session.status == models.SessionStatus.in_progress:
        session = exam_engine.enforce_expiry(db, session)

    seconds_until_start = exam_engine.scheduled_time_until_start(global_state)

    # Determine phase statuses
    phase1_status = "not_started"
    phase2_status = "locked"
    session_status = None
    current_phase = 1
    phase1_submitted_at = None
    phase2_submitted_at = None
    time_remaining = 0
    has_active = False

    if session:
        session_status = session.status.value
        current_phase = session.current_phase
        phase1_submitted_at = session.phase1_submitted_at
        phase2_submitted_at = session.phase2_submitted_at

        if session.status == models.SessionStatus.in_progress:
            has_active = True
            time_remaining = exam_engine.time_remaining_seconds(session)
            if session.phase1_submitted_at is None:
                phase1_status = "in_progress"
                phase2_status = "locked"
            else:
                phase1_status = "completed"
                if session.domain_selected:
                    phase2_status = "in_progress"
                else:
                    phase2_status = "ready"
        elif session.status in (models.SessionStatus.completed, models.SessionStatus.expired):
            phase1_status = "completed"
            phase2_status = "completed" if session.phase2_submitted_at else "locked"
        elif session.status == models.SessionStatus.disqualified:
            phase1_status = "completed" if session.phase1_submitted_at else "in_progress"
            phase2_status = "locked"
        elif session.status == models.SessionStatus.not_started:
            phase1_status = "not_started"

    return schemas.CandidateDashboardOut(
        full_name=user.full_name,
        email=user.email,
        exam_open=exam_engine.is_exam_window_open(global_state),
        scheduled_start_at=global_state.scheduled_start_at,
        scheduled_end_at=global_state.scheduled_end_at,
        timezone_label=global_state.timezone_label,
        seconds_until_start=seconds_until_start,
        session_status=session_status,
        current_phase=current_phase,
        phase1_status=phase1_status,
        phase2_status=phase2_status,
        phase1_submitted_at=phase1_submitted_at,
        phase2_submitted_at=phase2_submitted_at,
        time_remaining_seconds=time_remaining,
        has_active_session=has_active,
    )


# ============================================================
# Exam start / state / question
# ============================================================

@router.post("/start", response_model=schemas.QuestionOut)
async def start_exam(db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    global_state = exam_engine.get_global_exam_state(db)
    if not exam_engine.is_exam_window_open(global_state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The examination has not been opened yet. Please wait for the administrator to start the exam.",
        )
    try:
        session = exam_engine.start_session(db, user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session is {session.status.value}")

    # Start at the appropriate index for the current phase
    start_index = session.current_index
    q = exam_engine.get_question_for_display(db, session, start_index)
    await _broadcast_candidate_update(db, session, user)
    return q


@router.get("/state", response_model=schemas.ExamStateOut)
def get_state(db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    global_state = exam_engine.get_global_exam_state(db)
    session = exam_engine.get_active_session(db, user)
    if not session:
        return schemas.ExamStateOut(
            status="not_started",
            exam_open=exam_engine.is_exam_window_open(global_state),
            domain_selected=False,
            paper2_domain=None,
            time_remaining_seconds=0,
            current_index=0,
            total_questions=0,
            phase1_count=EXAM_PAPER1_COUNT,
            strikes=0,
            max_strikes=MAX_STRIKES,
            nav=[],
            current_phase=1,
            phase1_submitted=False,
            scheduled_start_at=global_state.scheduled_start_at,
            scheduled_end_at=global_state.scheduled_end_at,
        )
    session = exam_engine.enforce_expiry(db, session)
    nav = exam_engine.build_nav(db, session)
    return schemas.ExamStateOut(
        status=session.status.value,
        exam_open=exam_engine.is_exam_window_open(global_state),
        domain_selected=session.domain_selected,
        paper2_domain=session.paper2_domain,
        time_remaining_seconds=exam_engine.time_remaining_seconds(session),
        current_index=session.current_index,
        total_questions=len(session.question_order),
        phase1_count=EXAM_PAPER1_COUNT,
        strikes=session.strikes,
        max_strikes=MAX_STRIKES,
        nav=nav,
        current_phase=session.current_phase,
        phase1_submitted=session.phase1_submitted_at is not None,
        phase1_submitted_at=session.phase1_submitted_at,
        scheduled_start_at=global_state.scheduled_start_at,
        scheduled_end_at=global_state.scheduled_end_at,
    )


@router.get("/question/{index}", response_model=schemas.QuestionOut)
def get_question(
    index: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    global_state = exam_engine.get_global_exam_state(db)
    if not global_state.exam_open:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The examination has been closed by the administrator.",
        )
    session = _require_active_session(db, user)
    try:
        return exam_engine.get_question_for_display(db, session, index)
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")
    except exam_engine.PhaseLockedError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Phase 1 is permanently locked")


# ============================================================
# Answer, Navigation
# ============================================================

@router.post("/answer")
async def submit_answer(
    payload: schemas.AnswerSubmit,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    session = _require_active_session(db, user)
    try:
        exam_engine.save_answer(
            db, session, payload.index,
            payload.selected_display_index, payload.written_text, payload.marked_for_review,
        )
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")
    except exam_engine.PhaseLockedError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Phase 1 is permanently locked")
    await _broadcast_candidate_update(db, session, user)
    return {"ok": True}


@router.post("/navigate")
async def navigate(
    payload: schemas.NavigateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    session = _require_active_session(db, user)
    try:
        new_index = exam_engine.navigate(db, session, payload.direction, payload.jump_to)
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")
    except exam_engine.PhaseLockedError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Phase 1 is permanently locked")
    try:
        q = exam_engine.get_question_for_display(db, session, new_index)
    except exam_engine.PhaseLockedError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Phase 1 is permanently locked")
    return q


# ============================================================
# Phase Submission
# ============================================================

@router.post("/submit-phase1")
async def submit_phase1(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    """
    Candidate voluntarily submits Phase 1.
    Session remains in_progress — candidate returns to dashboard
    and can start Phase 2 from there.
    No score is returned to the candidate.
    """
    session = _require_active_session(db, user)

    if session.phase1_submitted_at is not None:
        # Already submitted — idempotent, just return ok
        return {"status": "already_submitted", "current_phase": session.current_phase}

    session = exam_engine.submit_phase1(db, session)
    await _broadcast_candidate_update(db, session, user)
    return {
        "status": "phase1_submitted",
        "current_phase": session.current_phase,
        # Do NOT return score to candidate
    }


@router.post("/submit-phase2")
async def submit_phase2(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    """
    Candidate voluntarily submits Phase 2 / the full exam.
    Session is marked completed. No score returned to candidate.
    """
    session = _require_active_session(db, user)

    if not session.domain_selected or len(session.question_order) <= EXAM_PAPER1_COUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phase 2 has not been started. Select a domain first.",
        )

    session = exam_engine.submit_phase2(db, session)
    await _broadcast_candidate_update(db, session, user)
    return {"status": session.status.value}


@router.post("/submit")
async def submit_exam(db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    """Legacy single-submit endpoint for backward compatibility."""
    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.submit_session(db, session)
    await _broadcast_candidate_update(db, session, user)
    return {"status": session.status.value}


# ============================================================
# Domain Selection
# ============================================================

@router.post("/select-domain")
async def select_domain(
    payload: schemas.SelectDomainRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    """
    Selects domain and loads Phase 2 questions.
    Phase 1 must have been submitted first.
    """
    session = _require_active_session(db, user)

    if session.phase1_submitted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phase 1 must be submitted before selecting a domain.",
        )

    canonical_domain = normalise_domain(payload.domain)
    try:
        try:
            count = _ensure_domain_questions_loaded(db, canonical_domain)
            if count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No questions found for '{canonical_domain}'. "
                           f"Please contact the administrator.",
                )
        except ValueError as load_err:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(load_err))

        session = exam_engine.select_domain(db, session, canonical_domain)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await _broadcast_candidate_update(db, session, user)
    return {
        "status": "ok",
        "domain_selected": True,
        "paper2_domain": session.paper2_domain,
        "total_questions": len(session.question_order),
        "phase2_start_index": EXAM_PAPER1_COUNT,
    }


# ============================================================
# Violations
# ============================================================

@router.post("/violation", response_model=schemas.ViolationResult)
async def report_violation(
    payload: schemas.ViolationReport,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    if payload.violation_type not in VALID_VIOLATION_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown violation type")

    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        return schemas.ViolationResult(
            strikes=session.strikes, max_strikes=MAX_STRIKES,
            action="ignored", violation_type=payload.violation_type,
        )

    result = exam_engine.register_violation(db, session, payload.violation_type)
    await _broadcast_candidate_update(db, session, user)
    # action is always "warn" — violations never auto-submit or auto-disqualify
    return schemas.ViolationResult(
        strikes=result["strikes"], max_strikes=MAX_STRIKES,
        action=result["action"], violation_type=payload.violation_type,
    )


# ============================================================
# Camera health advisory (NOT a violation — see schemas.CameraStatusReport)
# ============================================================

@router.post("/camera-status", response_model=schemas.CameraStatusResult)
async def report_camera_status(
    payload: schemas.CameraStatusReport,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    """
    Plain HTTP (not the WebSocket) so a candidate whose camera fails to
    acquire — NotAllowedError, NotFoundError, etc. — still gets this
    reported even if the proctor WebSocket hasn't connected yet. This
    never touches strikes/violations: it's purely advisory so the admin
    grid can show "Camera Active" vs "Camera Blocked" and a human proctor
    can decide what to do, per the spec that a camera failure must never
    stop or lock the candidate out of the exam.
    """
    if payload.camera_status not in ("active", "blocked"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="camera_status must be 'active' or 'blocked'")

    session = exam_engine.get_active_session(db, user)
    if session:
        try:
            await manager.broadcast({
                "type": "camera_advisory",
                "user_id": user.id,
                "session_id": session.id,
                "camera_status": payload.camera_status,
                "reason": payload.reason,
            })
        except Exception:
            # Advisory delivery is best-effort — never let a broadcast
            # hiccup surface as a failure to the candidate.
            pass
    return schemas.CameraStatusResult(ok=True)


# ============================================================
# WebSocket — candidate
# ============================================================
@router.websocket("/ws/candidate")
async def candidate_websocket(
    websocket: WebSocket,
    token: str,
    db: Session = Depends(get_db),
):
    user = None

    try:
        user = get_current_user(token, db)

        if user.role != models.UserRole.candidate:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION
            )
            return

        session = exam_engine.get_active_session(db, user)

        if not session or session.status != models.SessionStatus.in_progress:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION
            )
            return

        session_id = session.id

        await manager.connect_candidate(user.id, websocket)

        while True:
            try:
                # A bounded receive lets us periodically re-check the
                # session is still legitimately in_progress even while the
                # candidate's client is idle (proctor frames are throttled
                # client-side, so long quiet gaps are normal) — a dropped
                # campus-wifi connection or an admin disqualification
                # in the meantime then closes this socket instead of
                # leaving it dangling until the OS eventually notices.
                try:
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(), timeout=CANDIDATE_WS_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    fresh = (
                        db.query(models.ExamSession)
                        .filter(models.ExamSession.id == session_id)
                        .first()
                    )
                    if not fresh or fresh.status != models.SessionStatus.in_progress:
                        break
                    continue

                try:
                    data = json.loads(msg_text)

                    if not isinstance(data, dict):
                        continue

                    msg_type = data.get("type")

                    if msg_type == "proctor_frame":
                        frame_data = data.get("frame", "")

                        if frame_data:
                            await manager.handle_proctor_frame(
                                user.id,
                                session_id,
                                frame_data,
                            )

                    elif msg_type in [
                        "offer",
                        "answer",
                        "ice_candidate",
                    ]:
                        data["user_id"] = user.id
                        data["session_id"] = session_id

                        await manager.send_to_admins(data)

                except json.JSONDecodeError:
                    pass

            except WebSocketDisconnect:
                break

            except Exception:
                # Any other transport-level hiccup (a reset connection, a
                # malformed frame the ASGI server itself chokes on, …) ends
                # THIS candidate's socket only — it must never propagate
                # out and take down the worker process or any other
                # candidate's connection.
                break

    except WebSocketDisconnect:
        pass

    except Exception:
        pass

    finally:
        if user is not None:
            try:
                await manager.disconnect_candidate(user.id)
            except Exception:
                pass 
