"""
All exam truth lives here and in the database — never in the browser.

Architecture:
  - Phase 1 (Research Methodology) loads at session start.
  - Candidate submits Phase 1 explicitly → phase1_submitted_at is set,
    current_phase advances to 2. Session stays in_progress.
  - Candidate returns to dashboard, selects domain, then starts Phase 2.
  - Phase 2 questions are appended when domain is confirmed.
  - Candidate submits Phase 2 → session completes.
  - Global hard-stop (scheduled_end_at) is authoritative for timing.
    A candidate who starts at 10:30 when end is 12:00 gets 90 min, not 120.

VIOLATIONS NEVER AUTO-SUBMIT OR AUTO-DISQUALIFY.
Only an authenticated admin can disqualify a candidate.
"""
import random
from datetime import datetime, timedelta
from typing import Optional

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend import models
from backend.config import EXAM_DURATION_MINUTES, EXAM_PAPER1_COUNT, EXAM_PAPER2_COUNT

PHASE_LABELS = {1: "Part 1 — Research Methodology", 2: "Part 2 — Domain Specific"}


# ============================================================
# Global exam state helpers
# ============================================================

def _get_or_create_global_state(db: Session) -> models.ExamGlobalState:
    """Ensure the singleton ExamGlobalState row exists."""
    state = db.query(models.ExamGlobalState).filter(models.ExamGlobalState.id == 1).first()
    if not state:
        state = models.ExamGlobalState(id=1, exam_open=False)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def get_global_exam_state(db: Session) -> models.ExamGlobalState:
    return _get_or_create_global_state(db)


def set_global_exam_open(db: Session, open: bool) -> models.ExamGlobalState:
    state = _get_or_create_global_state(db)
    state.exam_open = open
    if open:
        state.opened_at = datetime.utcnow()
        state.closed_at = None
    else:
        state.closed_at = datetime.utcnow()
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def set_exam_schedule(
    db: Session,
    scheduled_start_at: datetime,
    scheduled_end_at: datetime,
    timezone_label: str = "Asia/Kolkata",
) -> models.ExamGlobalState:
    """Set the scheduled exam window. Both timestamps should be in UTC."""
    state = _get_or_create_global_state(db)
    state.scheduled_start_at = scheduled_start_at
    state.scheduled_end_at = scheduled_end_at
    state.timezone_label = timezone_label
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


# ============================================================
# Timing helpers — server-authoritative
# ============================================================

def compute_exam_ends_at(global_state: models.ExamGlobalState, now: datetime) -> datetime:
    """
    Compute when this session should end, respecting the global hard-stop.

    If a scheduled_end_at is configured:
        - early entry  (now < scheduled_start_at): gets full window
        - on-time      (now == scheduled_start_at): gets full window
        - late entry   (now > scheduled_start_at): gets reduced window
        - past end     (now >= scheduled_end_at): gets 0 seconds
    If no scheduled_end_at is configured, fall back to the old behaviour
    (now + EXAM_DURATION_MINUTES).
    """
    if global_state.scheduled_end_at:
        # The candidate's effective start is the later of now or scheduled start
        effective_start = max(
            now,
            global_state.scheduled_start_at or now,
        )
        # But exam_ends_at can never exceed the global hard-stop
        return min(effective_start + timedelta(minutes=EXAM_DURATION_MINUTES),
                   global_state.scheduled_end_at)
    else:
        # Fallback: no schedule configured, give full duration
        return now + timedelta(minutes=EXAM_DURATION_MINUTES)


def time_remaining_seconds(session: models.ExamSession) -> int:
    if not session.exam_ends_at:
        return 0
    delta = (session.exam_ends_at - datetime.utcnow()).total_seconds()
    return max(0, int(delta))


def scheduled_time_until_start(global_state: models.ExamGlobalState) -> int:
    """
    Seconds until the scheduled exam start. Returns 0 if already started or no schedule.
    Used for the early-entry countdown on the candidate dashboard.
    """
    if not global_state.scheduled_start_at:
        return 0
    delta = (global_state.scheduled_start_at - datetime.utcnow()).total_seconds()
    return max(0, int(delta))


def is_exam_window_open(global_state: models.ExamGlobalState) -> bool:
    """
    True if the current time is within the scheduled window AND exam_open is True.
    Both conditions must hold for a candidate to start.
    """
    if not global_state.exam_open:
        return False
    now = datetime.utcnow()
    if global_state.scheduled_end_at and now >= global_state.scheduled_end_at:
        return False
    return True


# ============================================================
# Option shuffling
# ============================================================

def _shuffle_options_for_question(question: models.Question, rng: random.Random) -> list:
    if question.question_type != models.QuestionType.mcq or not question.options:
        return []
    n = len(question.options)
    mapping = list(range(n))
    rng.shuffle(mapping)
    return mapping


# ============================================================
# Session lifecycle
# ============================================================

def start_session(db: Session, user: models.User) -> models.ExamSession:
    """
    Start or resume a Phase 1 exam session for the given user.
    Raises ValueError if the exam window is not open or the candidate
    has already used their attempt (and retake has not been granted).
    """
    global_state = get_global_exam_state(db)
    if not is_exam_window_open(global_state):
        raise ValueError(
            "The examination has not been opened yet. Please wait for the administrator to start the exam."
        )

    # Block if we are past the scheduled end time
    now = datetime.utcnow()
    if global_state.scheduled_end_at and now >= global_state.scheduled_end_at:
        raise ValueError("The examination period has ended. No new sessions can be started.")

    # Resume an existing in-progress session (network reconnect)
    existing = (
        db.query(models.ExamSession)
        .filter(
            models.ExamSession.user_id == user.id,
            models.ExamSession.status == models.SessionStatus.in_progress,
        )
        .first()
    )
    if existing:
        return existing

    # Block re-attempts unless retake has been explicitly granted by admin
    already_used = (
        db.query(models.ExamSession)
        .filter(
            models.ExamSession.user_id == user.id,
            models.ExamSession.status.in_([
                models.SessionStatus.completed,
                models.SessionStatus.disqualified,
                models.SessionStatus.expired,
            ]),
        )
        .first()
    )
    if already_used and not already_used.retake_granted:
        raise ValueError("This candidate has already used their exam attempt.")

    # Load Phase 1 (Research Methodology) questions
    mcq_pool = (
        db.query(models.Question)
        .filter(models.Question.domain == "Research Methodology")
        .all()
    )
    if len(mcq_pool) < EXAM_PAPER1_COUNT:
        raise RuntimeError(
            f"Question bank isn't ready yet: need {EXAM_PAPER1_COUNT} MCQ for Research Methodology, "
            f"currently have {len(mcq_pool)}."
        )

    rng = random.Random(f"{user.id}-{now.timestamp()}")
    part1 = rng.sample(mcq_pool, EXAM_PAPER1_COUNT)

    option_orders = {}
    for q in part1:
        mapping = _shuffle_options_for_question(q, rng)
        if mapping:
            option_orders[str(q.id)] = mapping

    exam_ends_at = compute_exam_ends_at(global_state, now)

    session = models.ExamSession(
        user_id=user.id,
        status=models.SessionStatus.in_progress,
        started_at=now,
        exam_ends_at=exam_ends_at,
        question_order=[q.id for q in part1],
        option_orders=option_orders,
        current_index=0,
        current_phase=1,
        strikes=0,
        domain_selected=False,
        last_activity=now,
    )
    db.add(session)
    db.flush()

    for q in part1:
        db.add(models.Answer(session_id=session.id, question_id=q.id))

    db.commit()
    db.refresh(session)
    return session


def select_domain(db: Session, session: models.ExamSession, domain: str) -> models.ExamSession:
    """
    Append Phase 2 domain-specific questions to the session's question_order.
    This can only be done once per session.
    The candidate must have already submitted Phase 1 (current_phase == 2).
    """
    if session.phase1_submitted_at is None:
        raise ValueError("Phase 1 must be submitted before selecting a domain.")
    if session.domain_selected:
        raise ValueError("Domain has already been selected for this session.")

    pool = db.query(models.Question).filter(models.Question.domain == domain).all()
    if len(pool) < EXAM_PAPER2_COUNT:
        raise ValueError(f"Phase 2 questions for '{domain}' have not been uploaded yet.")

    rng = random.Random(f"{session.user_id}-domain-{datetime.utcnow().timestamp()}")
    part2 = rng.sample(pool, EXAM_PAPER2_COUNT)

    new_order = list(session.question_order)
    new_option_orders = dict(session.option_orders or {})

    for q in part2:
        mapping = _shuffle_options_for_question(q, rng)
        if mapping:
            new_option_orders[str(q.id)] = mapping
        new_order.append(q.id)
        db.add(models.Answer(session_id=session.id, question_id=q.id))

    session.question_order = new_order
    session.option_orders = new_option_orders
    session.paper2_domain = domain
    session.domain_selected = True
    # Jump current_index to first Phase 2 question
    session.current_index = EXAM_PAPER1_COUNT

    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_any_session(db: Session, user: models.User) -> Optional[models.ExamSession]:
    """Return the most recent session regardless of status (for dashboard display)."""
    return (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user.id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )


def get_active_session(db: Session, user: models.User) -> Optional[models.ExamSession]:
    return (
        db.query(models.ExamSession)
        .filter(
            models.ExamSession.user_id == user.id,
            models.ExamSession.status == models.SessionStatus.in_progress,
        )
        .order_by(models.ExamSession.id.desc())
        .first()
    )


def touch(db: Session, session: models.ExamSession):
    session.last_activity = datetime.utcnow()
    db.add(session)


def enforce_expiry(db: Session, session: models.ExamSession) -> models.ExamSession:
    """Auto-expire when the global hard-stop timer reaches zero."""
    if session.status == models.SessionStatus.in_progress and time_remaining_seconds(session) <= 0:
        # Grade whatever has been answered so far
        _save_phase_result(db, session, phase=session.current_phase, is_expiry=True)
        session.status = models.SessionStatus.expired
        session.submitted_at = datetime.utcnow()
        if session.current_phase == 2:
            session.phase2_submitted_at = session.submitted_at
        elif session.current_phase == 1:
            session.phase1_submitted_at = session.submitted_at
        # Compute combined score
        session.score = _compute_combined_score(db, session)
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


# ============================================================
# Question display
# ============================================================

def get_question_for_display(db: Session, session: models.ExamSession, index: int) -> dict:
    if index < 0 or index >= len(session.question_order):
        raise IndexError("Question index out of range")

    question_id = session.question_order[index]
    question = db.query(models.Question).filter(models.Question.id == question_id).first()
    answer = (
        db.query(models.Answer)
        .filter(
            models.Answer.session_id == session.id,
            models.Answer.question_id == question_id,
        )
        .first()
    )

    display_options = None
    if question.question_type == models.QuestionType.mcq:
        mapping = session.option_orders.get(str(question.id), list(range(len(question.options))))
        display_options = [question.options[canon_idx] for canon_idx in mapping]

    if not answer.visited:
        answer.visited = True
        db.add(answer)
        db.commit()

    phase = 1 if index < EXAM_PAPER1_COUNT else 2

    return {
        "index": index,
        "total": len(session.question_order),
        "phase": phase,
        "phase_label": PHASE_LABELS[phase],
        "question_type": question.question_type.value,
        "question_text": question.question_text,
        "options": display_options,
        "selected_display_index": answer.selected_display_index,
        "written_text": answer.written_text,
        "marked_for_review": answer.marked_for_review,
    }


def build_nav(db: Session, session: models.ExamSession) -> list:
    answers = {
        a.question_id: a
        for a in db.query(models.Answer).filter(models.Answer.session_id == session.id).all()
    }
    nav = []
    for i, qid in enumerate(session.question_order):
        a = answers.get(qid)
        answered = bool(a and (a.selected_display_index is not None or (a.written_text or "").strip()))
        nav.append({
            "index": i,
            "phase": 1 if i < EXAM_PAPER1_COUNT else 2,
            "answered": answered,
            "marked_for_review": bool(a and a.marked_for_review),
            "visited": bool(a and a.visited),
        })
    return nav


# ============================================================
# Answer + navigation
# ============================================================

def save_answer(
    db: Session,
    session: models.ExamSession,
    index: int,
    selected_display_index: Optional[int],
    written_text: Optional[str],
    marked_for_review: Optional[bool],
):
    if index < 0 or index >= len(session.question_order):
        raise IndexError("Question index out of range")
    question_id = session.question_order[index]
    answer = (
        db.query(models.Answer)
        .filter(
            models.Answer.session_id == session.id,
            models.Answer.question_id == question_id,
        )
        .first()
    )
    if selected_display_index is not None:
        answer.selected_display_index = selected_display_index
    if written_text is not None:
        answer.written_text = written_text
    if marked_for_review is not None:
        answer.marked_for_review = marked_for_review
    answer.updated_at = datetime.utcnow()
    db.add(answer)
    touch(db, session)
    db.commit()


def navigate(
    db: Session,
    session: models.ExamSession,
    direction: Optional[str],
    jump_to: Optional[int],
) -> int:
    total = len(session.question_order)
    new_index = session.current_index
    if jump_to is not None:
        if jump_to < 0 or jump_to >= total:
            raise IndexError("Question index out of range")
        new_index = jump_to
    elif direction == "next":
        new_index = min(total - 1, session.current_index + 1)
    elif direction == "prev":
        new_index = max(0, session.current_index - 1)
    session.current_index = new_index
    touch(db, session)
    db.commit()
    return new_index


# ============================================================
# Violations — NEVER auto-submit, NEVER auto-disqualify
# ============================================================

def register_violation(db: Session, session: models.ExamSession, violation_type: str) -> dict:
    """
    Record a violation event. Always returns action="warn".
    Only an authenticated admin can disqualify a candidate — never automatic.
    """
    if session.status != models.SessionStatus.in_progress:
        return {"strikes": session.strikes, "action": "ignored_session_not_active"}

    session.strikes += 1
    db.add(models.Violation(
        session_id=session.id,
        violation_type=violation_type,
        strike_number=session.strikes,
    ))

    # WARNING: No auto-disqualification regardless of strike count.
    # The administrator reviews violations and decides on action.
    action = "warn"

    touch(db, session)
    db.commit()
    db.refresh(session)
    return {"strikes": session.strikes, "action": action}


def admin_disqualify(db: Session, session: models.ExamSession, user: models.User) -> models.ExamSession:
    """
    Admin-only disqualification. Saves current answers, grades what exists,
    and revokes the candidate's JWT by bumping token_version.
    """
    if session.status == models.SessionStatus.in_progress:
        # Save a result for whatever phase they're in
        _save_phase_result(db, session, phase=session.current_phase, is_expiry=True)
        session.score = _compute_combined_score(db, session)
        session.status = models.SessionStatus.disqualified
        session.submitted_at = datetime.utcnow()
        db.add(session)

    # Revoke all current JWTs for this user immediately
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()
    db.refresh(session)
    return session


# ============================================================
# Grading — internal, admin-only
# ============================================================

def _grade_phase(
    db: Session,
    session: models.ExamSession,
    phase: int,
) -> tuple[int, int, int, int, list]:
    """
    Grade a specific phase. Returns (attempted, correct, incorrect, unanswered, question_detail).
    question_detail is a JSON-serialisable list for CandidateResult.question_detail.
    """
    if phase == 1:
        q_ids = session.question_order[:EXAM_PAPER1_COUNT]
    else:
        q_ids = session.question_order[EXAM_PAPER1_COUNT:]

    answers_map = {
        a.question_id: a
        for a in db.query(models.Answer)
        .filter(
            models.Answer.session_id == session.id,
            models.Answer.question_id.in_(q_ids),
        )
        .all()
    }
    questions_map = {
        q.id: q
        for q in db.query(models.Question)
        .filter(models.Question.id.in_(q_ids))
        .all()
    }

    attempted = 0
    correct = 0
    incorrect = 0
    unanswered = 0
    detail = []

    for qid in q_ids:
        ans = answers_map.get(qid)
        q = questions_map.get(qid)
        if not q:
            continue

        is_answered = bool(ans and (
            ans.selected_display_index is not None or
            (ans.written_text or "").strip()
        ))

        if is_answered:
            attempted += 1
        else:
            unanswered += 1

        is_correct = False
        candidate_answer_text = None
        correct_answer_text = None
        display_idx = None
        canonical_idx = None

        if q.question_type == models.QuestionType.mcq and q.correct_option is not None:
            if ans and ans.selected_display_index is not None:
                display_idx = ans.selected_display_index
                mapping = session.option_orders.get(str(q.id), list(range(len(q.options))))
                if display_idx < len(mapping):
                    canonical_idx = mapping[display_idx]
                    if canonical_idx == q.correct_option:
                        is_correct = True
                        correct += 1
                    else:
                        incorrect += 1
                    if display_idx < len(q.options):
                        candidate_answer_text = q.options[canonical_idx]
            if q.correct_option < len(q.options):
                correct_answer_text = q.options[q.correct_option]

        detail.append({
            "question_id": qid,
            "question_text": q.question_text,
            "display_index_chosen": display_idx,
            "canonical_index_chosen": canonical_idx,
            "correct_option": q.correct_option,
            "is_correct": is_correct,
            "is_answered": is_answered,
            "candidate_answer_text": candidate_answer_text,
            "correct_answer_text": correct_answer_text,
            "marks": 1 if is_correct else 0,
        })

    return attempted, correct, incorrect, unanswered, detail


def _save_phase_result(
    db: Session,
    session: models.ExamSession,
    phase: int,
    is_expiry: bool = False,
) -> models.CandidateResult:
    """
    Create (or update) a CandidateResult row for the given phase.
    Idempotent: if a result already exists for this phase, it is overwritten.
    """
    # Remove any existing result for this phase (idempotency)
    existing = (
        db.query(models.CandidateResult)
        .filter(
            models.CandidateResult.session_id == session.id,
            models.CandidateResult.phase == phase,
        )
        .first()
    )
    if existing:
        db.delete(existing)
        db.flush()

    total = EXAM_PAPER1_COUNT if phase == 1 else EXAM_PAPER2_COUNT
    attempted, correct, incorrect, unanswered, detail = _grade_phase(db, session, phase)

    accuracy = round((correct / total) * 100, 2) if total > 0 else 0.0

    domain = None if phase == 1 else session.paper2_domain

    result = models.CandidateResult(
        session_id=session.id,
        phase=phase,
        domain=domain,
        total_questions=total,
        attempted=attempted,
        correct=correct,
        incorrect=incorrect,
        unanswered=unanswered,
        score=correct,
        max_score=total,
        accuracy=accuracy,
        submitted_at=datetime.utcnow(),
        question_detail=detail,
    )
    db.add(result)
    db.flush()
    return result


def _compute_combined_score(db: Session, session: models.ExamSession) -> int:
    """Sum scores from all CandidateResult rows for this session."""
    results = (
        db.query(models.CandidateResult)
        .filter(models.CandidateResult.session_id == session.id)
        .all()
    )
    return sum(r.score for r in results)


def _grade_session(db: Session, session: models.ExamSession):
    """
    Full session grading: grade all available phases and update session.score.
    Called on manual submit or expiry.
    """
    _save_phase_result(db, session, phase=1)
    if session.domain_selected and len(session.question_order) > EXAM_PAPER1_COUNT:
        _save_phase_result(db, session, phase=2)
    session.score = _compute_combined_score(db, session)


# ============================================================
# Phase submission
# ============================================================

def submit_phase1(db: Session, session: models.ExamSession) -> models.ExamSession:
    """
    Candidate voluntarily submits Phase 1.
    - Grades Phase 1 and stores CandidateResult.
    - Advances current_phase to 2.
    - Session remains in_progress (Phase 2 is not yet started).
    - Does NOT complete the session.
    """
    if session.phase1_submitted_at is not None:
        # Idempotent — already submitted
        return session

    _save_phase_result(db, session, phase=1)
    session.phase1_submitted_at = datetime.utcnow()
    session.current_phase = 2
    # Move index to the boundary so dashboard shows Phase 1 complete
    session.current_index = EXAM_PAPER1_COUNT
    session.score = _compute_combined_score(db, session)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def submit_phase2(db: Session, session: models.ExamSession) -> models.ExamSession:
    """
    Candidate voluntarily submits Phase 2 (or the full exam if Phase 2 is active).
    - Grades Phase 2 and stores CandidateResult.
    - Marks session as completed.
    """
    if session.status != models.SessionStatus.in_progress:
        return session

    _save_phase_result(db, session, phase=2)
    session.phase2_submitted_at = datetime.utcnow()
    session.status = models.SessionStatus.completed
    session.submitted_at = session.phase2_submitted_at
    session.score = _compute_combined_score(db, session)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def submit_session(db: Session, session: models.ExamSession) -> models.ExamSession:
    """
    Generic full-exam submit. Called from the old single-submit endpoint
    for backward compatibility. Routes to the correct phase submit.
    """
    if session.status != models.SessionStatus.in_progress:
        return session

    if session.phase1_submitted_at is None:
        # Submitting before Phase 1 is done — treat as Phase 1 submit
        submit_phase1(db, session)
        db.refresh(session)

    if session.domain_selected and len(session.question_order) > EXAM_PAPER1_COUNT:
        return submit_phase2(db, session)
    else:
        # Phase 2 not started — mark as completed with Phase 1 only
        session.status = models.SessionStatus.completed
        session.submitted_at = datetime.utcnow()
        session.score = _compute_combined_score(db, session)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session


def force_end_all_sessions(db: Session) -> int:
    """
    Admin-triggered global exam end. Force-expires every in-progress session
    and grades them. Returns the count of sessions that were ended.
    """
    active_sessions = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.status == models.SessionStatus.in_progress)
        .all()
    )
    now = datetime.utcnow()
    count = 0
    for s in active_sessions:
        _grade_session(db, s)
        s.status = models.SessionStatus.expired
        s.submitted_at = now
        if s.current_phase == 1 and not s.phase1_submitted_at:
            s.phase1_submitted_at = now
        if s.current_phase == 2 and not s.phase2_submitted_at:
            s.phase2_submitted_at = now
        db.add(s)
        count += 1
    db.commit()
    return count
