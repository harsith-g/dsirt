"""Score computation.

Called exactly once per phase, inside the same transaction that flips the
session status, so a double-submit cannot double-score.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CandidateAnswer,
    ExamResult,
    ExamSession,
    Phase,
    Question,
    QuestionType,
)


@dataclass(slots=True)
class PhaseScore:
    score: float = 0.0
    max_score: float = 0.0
    attempted: int = 0
    correct: int = 0


async def score_phase(
    db: AsyncSession, session: ExamSession, phase: Phase
) -> PhaseScore:
    """Grade every allocated question for ``phase`` and persist per-answer marks.

    Unattempted questions score 0 and never attract the negative mark.
    Partial credit is not awarded for MULTIPLE_CHOICE: the selected set must
    match the correct set exactly, otherwise the negative mark applies.
    """
    allocated: list[str] = session.question_order.get(phase.value, [])
    if not allocated:
        return PhaseScore()

    question_ids = [uuid.UUID(q) for q in allocated]
    q_rows = (
        (
            await db.execute(
                select(Question)
                .options(selectinload(Question.options))
                .where(Question.id.in_(question_ids))
            )
        )
        .scalars()
        .all()
    )
    questions = {q.id: q for q in q_rows}

    a_rows = (
        (
            await db.execute(
                select(CandidateAnswer).where(
                    CandidateAnswer.session_id == session.id,
                    CandidateAnswer.phase == phase,
                )
            )
        )
        .scalars()
        .all()
    )
    answers = {a.question_id: a for a in a_rows}

    result = PhaseScore()
    for qid in question_ids:
        question = questions.get(qid)
        if question is None:
            continue
        result.max_score += question.marks

        answer = answers.get(qid)
        if answer is None:
            continue

        selected = {str(s) for s in (answer.selected_option_ids or [])}
        has_text = bool((answer.text_answer or "").strip())
        if not selected and not has_text:
            # Touched (e.g. marked for review) but left blank -- not attempted.
            answer.is_correct = None
            answer.awarded_marks = 0.0
            continue

        result.attempted += 1

        if question.question_type is QuestionType.TEXT:
            # Descriptive answers are graded by a human; recorded, not scored.
            answer.is_correct = None
            answer.awarded_marks = 0.0
            continue

        correct = {str(o.id) for o in question.options if o.is_correct}
        is_correct = bool(correct) and selected == correct
        answer.is_correct = is_correct
        if is_correct:
            answer.awarded_marks = question.marks
            result.score += question.marks
            result.correct += 1
        else:
            answer.awarded_marks = -question.negative_marks
            result.score -= question.negative_marks

    # A candidate can never finish below zero.
    result.score = max(0.0, round(result.score, 2))
    result.max_score = round(result.max_score, 2)
    return result


async def upsert_result(
    db: AsyncSession,
    session: ExamSession,
    *,
    phase1: PhaseScore | None = None,
    phase2: PhaseScore | None = None,
) -> ExamResult:
    result = (
        await db.execute(select(ExamResult).where(ExamResult.session_id == session.id))
    ).scalar_one_or_none()
    if result is None:
        result = ExamResult(session_id=session.id)
        db.add(result)

    if phase1 is not None:
        result.phase1_score = phase1.score
        result.phase1_max = phase1.max_score
        result.phase1_attempted = phase1.attempted
        result.phase1_correct = phase1.correct
    if phase2 is not None:
        result.phase2_score = phase2.score
        result.phase2_max = phase2.max_score
        result.phase2_attempted = phase2.attempted
        result.phase2_correct = phase2.correct

    result.total_score = round((result.phase1_score or 0) + (result.phase2_score or 0), 2)
    result.total_max = round((result.phase1_max or 0) + (result.phase2_max or 0), 2)
    await db.flush()
    return result
