"""Exam lifecycle state machine.

Invariants enforced here (not in the browser):

* The server owns the clock. ``phase*_hard_stop`` is written once, at phase
  start, and every later request recomputes "time left" from it. A client that
  lies about elapsed time changes nothing.
* Phase 1 locks permanently on submit. There is no transition back.
* Domain selection is a one-way door, recorded with ``domain_locked_at``.
* Phase 2 questions are drawn with ``phase == PHASE_2``, so a Phase 1 question
  is structurally incapable of appearing in Phase 2.
* Question and option order are randomised once per candidate and then frozen
  in the session row, so a page refresh does not reshuffle the paper.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    CandidateAnswer,
    Domain,
    Exam,
    ExamSession,
    Phase,
    Question,
    QuestionOption,
    QuestionType,
    SessionStatus,
)
from app.services.scoring import score_phase, upsert_result


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExamStateError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_409_CONFLICT) -> None:
        super().__init__(status_code=code, detail=detail)


@dataclass(slots=True)
class TimeStatus:
    phase: Phase
    hard_stop: datetime | None
    seconds_left: int
    expired: bool


# --------------------------------------------------------------------- exam
async def get_active_exam(db: AsyncSession) -> Exam:
    exam = (
        await db.execute(
            select(Exam).where(Exam.is_active.is_(True)).order_by(Exam.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if exam is None:
        raise ExamStateError("No active examination is configured.", status.HTTP_404_NOT_FOUND)
    return exam


def _exam_window_open(exam: Exam, now: datetime) -> tuple[bool, str]:
    if exam.start_time and now < exam.start_time:
        return False, f"The examination opens at {exam.start_time.isoformat()}."
    if exam.hard_stop_time and now >= exam.hard_stop_time:
        return False, "The examination window has closed."
    return True, ""


# ------------------------------------------------------------------ sessions
async def get_or_create_session(
    db: AsyncSession,
    candidate_id: uuid.UUID,
    exam: Exam,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> ExamSession:
    session = (
        await db.execute(
            select(ExamSession)
            .options(selectinload(ExamSession.selected_domain))
            .where(
                ExamSession.candidate_id == candidate_id,
                ExamSession.exam_id == exam.id,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        session = ExamSession(
            candidate_id=candidate_id,
            exam_id=exam.id,
            current_phase=Phase.PHASE_1,
            status=SessionStatus.NOT_STARTED,
            question_order={},
            option_order={},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(session)
        await db.flush()
    return session


def time_status(session: ExamSession, phase: Phase | None = None) -> TimeStatus:
    phase = phase or session.current_phase
    hard_stop = (
        session.phase1_hard_stop if phase is Phase.PHASE_1 else session.phase2_hard_stop
    )
    if hard_stop is None:
        return TimeStatus(phase=phase, hard_stop=None, seconds_left=0, expired=False)
    delta = (hard_stop - utcnow()).total_seconds()
    return TimeStatus(
        phase=phase,
        hard_stop=hard_stop,
        seconds_left=max(0, int(delta)),
        expired=delta <= 0,
    )


# ------------------------------------------------------------- allocation
async def _allocate(
    db: AsyncSession,
    *,
    phase: Phase,
    domain_id: uuid.UUID | None,
    limit: int,
    seed: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Pick the questions for a phase and freeze a per-candidate ordering.

    The RNG is seeded from the session id so an allocation is reproducible for
    audit purposes while still differing between candidates.
    """
    stmt = (
        select(Question)
        .options(selectinload(Question.options))
        .where(Question.phase == phase, Question.is_active.is_(True))
    )
    stmt = (
        stmt.where(Question.domain_id.is_(None))
        if phase is Phase.PHASE_1
        else stmt.where(Question.domain_id == domain_id)
    )
    questions = list((await db.execute(stmt)).scalars().all())
    if not questions:
        raise ExamStateError(
            "No questions are available for this paper. Contact the exam cell.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    rng = random.Random(seed)
    # Sort by id first so the shuffle is deterministic for a given seed
    # regardless of the order Postgres happened to return rows in.
    questions.sort(key=lambda q: str(q.id))
    rng.shuffle(questions)
    chosen = questions[:limit] if limit and limit > 0 else questions

    order: list[str] = []
    option_order: dict[str, list[str]] = {}
    for q in chosen:
        order.append(str(q.id))
        opts = sorted(q.options, key=lambda o: (o.display_order, str(o.id)))
        opt_ids = [str(o.id) for o in opts]
        # TRUE_FALSE keeps its natural order; everything else is shuffled.
        if q.question_type is not QuestionType.TRUE_FALSE:
            rng.shuffle(opt_ids)
        option_order[str(q.id)] = opt_ids
    return order, option_order


# ---------------------------------------------------------------- phase one
async def start_phase1(db: AsyncSession, session: ExamSession, exam: Exam) -> ExamSession:
    now = utcnow()
    open_, reason = _exam_window_open(exam, now)
    if not open_:
        raise ExamStateError(reason, status.HTTP_403_FORBIDDEN)

    if session.status is SessionStatus.PHASE_1_IN_PROGRESS:
        return session  # idempotent resume
    if session.status is not SessionStatus.NOT_STARTED:
        raise ExamStateError("Phase 1 has already been completed and is locked.")

    order, option_order = await _allocate(
        db,
        phase=Phase.PHASE_1,
        domain_id=None,
        limit=exam.phase1_question_count,
        seed=f"{session.id}:PHASE_1",
    )

    hard_stop = now + timedelta(minutes=exam.phase1_duration_mins)
    if exam.hard_stop_time:
        hard_stop = min(hard_stop, exam.hard_stop_time)

    session.question_order = {**session.question_order, Phase.PHASE_1.value: order}
    session.option_order = {**session.option_order, Phase.PHASE_1.value: option_order}
    session.phase1_start = now
    session.phase1_hard_stop = hard_stop
    session.current_phase = Phase.PHASE_1
    session.status = SessionStatus.PHASE_1_IN_PROGRESS
    await db.flush()
    return session


async def submit_phase1(
    db: AsyncSession, session: ExamSession, *, auto: bool = False
) -> ExamSession:
    if session.status is SessionStatus.PHASE_1_COMPLETED or session.status in (
        SessionStatus.PHASE_2_IN_PROGRESS,
        SessionStatus.COMPLETED,
    ):
        return session  # already locked; submitting twice is a no-op
    if session.status is not SessionStatus.PHASE_1_IN_PROGRESS:
        raise ExamStateError("Phase 1 is not in progress.")

    phase1 = await score_phase(db, session, Phase.PHASE_1)
    await upsert_result(db, session, phase1=phase1)
    session.phase1_end = utcnow()
    session.status = SessionStatus.PHASE_1_COMPLETED
    await db.flush()
    return session


# -------------------------------------------------------------- domain lock
async def select_domain(
    db: AsyncSession, session: ExamSession, domain_id: uuid.UUID
) -> ExamSession:
    if session.status is not SessionStatus.PHASE_1_COMPLETED:
        raise ExamStateError("Complete Phase 1 before selecting a domain.")
    if session.selected_domain_id is not None:
        raise ExamStateError("Your domain selection is already locked and cannot be changed.")

    domain = await db.get(Domain, domain_id)
    if domain is None or not domain.is_active:
        raise ExamStateError("That domain is not available.", status.HTTP_400_BAD_REQUEST)

    available = (
        await db.execute(
            select(func.count(Question.id)).where(
                Question.phase == Phase.PHASE_2,
                Question.domain_id == domain_id,
                Question.is_active.is_(True),
            )
        )
    ).scalar_one()
    if not available:
        raise ExamStateError(
            f"The {domain.name} paper has no questions loaded. Choose another domain.",
            status.HTTP_400_BAD_REQUEST,
        )

    session.selected_domain_id = domain_id
    session.domain_locked_at = utcnow()
    await db.flush()
    return session


async def list_selectable_domains(db: AsyncSession) -> list[tuple[Domain, int]]:
    """Active domains that actually have a Phase 2 paper behind them."""
    rows = (
        await db.execute(
            select(Domain, func.count(Question.id))
            .join(
                Question,
                (Question.domain_id == Domain.id)
                & (Question.phase == Phase.PHASE_2)
                & (Question.is_active.is_(True)),
                isouter=True,
            )
            .where(Domain.is_active.is_(True))
            .group_by(Domain.id)
            .order_by(Domain.name)
        )
    ).all()
    return [(d, int(c)) for d, c in rows if c]


# ---------------------------------------------------------------- phase two
async def start_phase2(db: AsyncSession, session: ExamSession, exam: Exam) -> ExamSession:
    now = utcnow()
    open_, reason = _exam_window_open(exam, now)
    if not open_:
        raise ExamStateError(reason, status.HTTP_403_FORBIDDEN)

    if session.status is SessionStatus.PHASE_2_IN_PROGRESS:
        return session
    if session.status is SessionStatus.COMPLETED:
        raise ExamStateError("The examination is already complete.")
    if session.status is not SessionStatus.PHASE_1_COMPLETED:
        raise ExamStateError("Complete Phase 1 first.")
    if session.selected_domain_id is None:
        raise ExamStateError("Select and lock a domain before starting Phase 2.")

    order, option_order = await _allocate(
        db,
        phase=Phase.PHASE_2,
        domain_id=session.selected_domain_id,
        limit=exam.phase2_question_count,
        seed=f"{session.id}:PHASE_2",
    )
    # Belt and braces: the allocator already filters on phase, but assert the
    # disjointness the spec calls out explicitly.
    phase1_ids = set(session.question_order.get(Phase.PHASE_1.value, []))
    overlap = phase1_ids.intersection(order)
    if overlap:  # pragma: no cover - structurally impossible
        raise ExamStateError("Allocation error: Phase 1 questions leaked into Phase 2.")

    hard_stop = now + timedelta(minutes=exam.phase2_duration_mins)
    if exam.hard_stop_time:
        hard_stop = min(hard_stop, exam.hard_stop_time)

    session.question_order = {**session.question_order, Phase.PHASE_2.value: order}
    session.option_order = {**session.option_order, Phase.PHASE_2.value: option_order}
    session.phase2_start = now
    session.phase2_hard_stop = hard_stop
    session.current_phase = Phase.PHASE_2
    session.status = SessionStatus.PHASE_2_IN_PROGRESS
    await db.flush()
    return session


async def submit_phase2(
    db: AsyncSession, session: ExamSession, *, auto: bool = False
) -> ExamSession:
    if session.status is SessionStatus.COMPLETED:
        return session
    if session.status is not SessionStatus.PHASE_2_IN_PROGRESS:
        raise ExamStateError("Phase 2 is not in progress.")

    phase2 = await score_phase(db, session, Phase.PHASE_2)
    await upsert_result(db, session, phase2=phase2)
    session.phase2_end = utcnow()
    session.status = SessionStatus.COMPLETED
    await db.flush()
    return session


async def enforce_deadline(db: AsyncSession, session: ExamSession) -> ExamSession:
    """Auto-submit a phase whose server-side deadline has passed.

    Called on every answer save and poll, so an abandoned or disconnected
    browser cannot leave a session hanging open past its hard stop.
    """
    if session.status is SessionStatus.PHASE_1_IN_PROGRESS:
        if time_status(session, Phase.PHASE_1).expired:
            return await submit_phase1(db, session, auto=True)
    elif session.status is SessionStatus.PHASE_2_IN_PROGRESS:
        if time_status(session, Phase.PHASE_2).expired:
            return await submit_phase2(db, session, auto=True)
    return session


# ------------------------------------------------------------------ answers
async def save_answer(
    db: AsyncSession,
    session: ExamSession,
    question_id: uuid.UUID,
    *,
    selected_option_ids: list[uuid.UUID] | None,
    text_answer: str | None,
    is_marked_for_review: bool,
) -> CandidateAnswer:
    """Server-side upsert for the debounced autosave."""
    if session.status not in (
        SessionStatus.PHASE_1_IN_PROGRESS,
        SessionStatus.PHASE_2_IN_PROGRESS,
    ):
        raise ExamStateError("This paper is locked; answers can no longer be saved.")

    phase = session.current_phase
    allocated = set(session.question_order.get(phase.value, []))
    if str(question_id) not in allocated:
        raise ExamStateError(
            "That question is not part of your paper.", status.HTTP_400_BAD_REQUEST
        )

    question = (
        await db.execute(
            select(Question)
            .options(selectinload(Question.options))
            .where(Question.id == question_id)
        )
    ).scalar_one_or_none()
    if question is None:
        raise ExamStateError("Unknown question.", status.HTTP_404_NOT_FOUND)

    # Only accept option ids that actually belong to this question.
    valid_option_ids = {o.id for o in question.options}
    cleaned = [str(o) for o in (selected_option_ids or []) if o in valid_option_ids]
    if question.question_type in (QuestionType.SINGLE_CHOICE, QuestionType.TRUE_FALSE):
        cleaned = cleaned[:1]

    answer = (
        await db.execute(
            select(CandidateAnswer).where(
                CandidateAnswer.session_id == session.id,
                CandidateAnswer.question_id == question_id,
            )
        )
    ).scalar_one_or_none()

    if answer is None:
        answer = CandidateAnswer(
            session_id=session.id,
            question_id=question_id,
            phase=phase,
        )
        db.add(answer)

    answer.selected_option_ids = cleaned
    answer.text_answer = (text_answer or None) if question.question_type is QuestionType.TEXT else None
    answer.is_marked_for_review = bool(is_marked_for_review)
    await db.flush()
    return answer


async def build_paper(db: AsyncSession, session: ExamSession) -> dict:
    """Render the candidate-facing paper: no ``is_correct`` ever leaves here."""
    phase = session.current_phase
    order: list[str] = session.question_order.get(phase.value, [])
    if not order:
        return {"phase": phase.value, "questions": []}

    opt_order: dict[str, list[str]] = session.option_order.get(phase.value, {})
    q_rows = (
        (
            await db.execute(
                select(Question)
                .options(selectinload(Question.options))
                .where(Question.id.in_([uuid.UUID(q) for q in order]))
            )
        )
        .scalars()
        .all()
    )
    questions = {str(q.id): q for q in q_rows}

    answers = {
        str(a.question_id): a
        for a in (
            await db.execute(
                select(CandidateAnswer).where(CandidateAnswer.session_id == session.id)
            )
        )
        .scalars()
        .all()
    }

    payload = []
    for index, qid in enumerate(order, start=1):
        q = questions.get(qid)
        if q is None:
            continue
        by_id = {str(o.id): o for o in q.options}
        ordered_ids = opt_order.get(qid) or [str(o.id) for o in q.options]
        answer = answers.get(qid)
        payload.append(
            {
                "number": index,
                "id": qid,
                "question_type": q.question_type.value,
                "question_text": q.question_text,
                "marks": q.marks,
                "negative_marks": q.negative_marks,
                "options": [
                    {"id": oid, "text": by_id[oid].option_text}
                    for oid in ordered_ids
                    if oid in by_id
                ],
                "selected_option_ids": list(answer.selected_option_ids) if answer else [],
                "text_answer": answer.text_answer if answer else None,
                "is_marked_for_review": bool(answer.is_marked_for_review) if answer else False,
            }
        )
    return {"phase": phase.value, "questions": payload}
