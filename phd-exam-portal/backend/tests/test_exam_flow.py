"""Integration tests for the exam state machine.

Requires Postgres and Redis. Inside the stack:

    docker compose exec backend python -m pytest tests/test_exam_flow.py -q
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.enums import Phase, SessionStatus
from app.services import exam_service as svc
from app.services.exam_service import ExamStateError, utcnow

pytestmark = pytest.mark.asyncio


async def _session(db, candidate, exam):
    return await svc.get_or_create_session(db, candidate.id, exam)


# ------------------------------------------------------------- allocation
async def test_phase1_allocates_and_freezes_order(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)

    order = s.question_order[Phase.PHASE_1.value]
    assert len(order) == exam.phase1_question_count == 5
    assert len(set(order)) == len(order)

    frozen = list(order)
    paper_a = await svc.build_paper(db, s)
    paper_b = await svc.build_paper(db, s)
    assert s.question_order[Phase.PHASE_1.value] == frozen
    assert [q["id"] for q in paper_a["questions"]] == [q["id"] for q in paper_b["questions"]]
    # And the option order is frozen too, so a refresh does not reshuffle.
    assert [o["id"] for o in paper_a["questions"][0]["options"]] == \
           [o["id"] for o in paper_b["questions"][0]["options"]]


async def test_different_candidates_get_different_orders(db, exam, phase1_questions):
    from app.core.security import hash_password
    from app.enums import CandidateStatus
    from app.models import Candidate

    orders = []
    for i in range(6):
        c = Candidate(
            registration_number=f"ORD{i:04d}",
            email=f"ord{i}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"Candidate {i}",
            status=CandidateStatus.APPROVED,
        )
        db.add(c)
        await db.flush()
        s = await svc.get_or_create_session(db, c.id, exam)
        await svc.start_phase1(db, s, exam)
        orders.append(tuple(s.question_order[Phase.PHASE_1.value]))
    assert len(set(orders)) > 1, "randomisation produced identical papers"


async def test_paper_never_exposes_the_correct_answer(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    paper = await svc.build_paper(db, s)
    blob = repr(paper)
    assert "is_correct" not in blob
    for q in paper["questions"]:
        for opt in q["options"]:
            assert set(opt) == {"id", "text"}


# ---------------------------------------------------------- server timer
async def test_hard_stop_is_server_set(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    before = utcnow()
    await svc.start_phase1(db, s, exam)
    expected = before + timedelta(minutes=exam.phase1_duration_mins)
    assert abs((s.phase1_hard_stop - expected).total_seconds()) < 5
    assert svc.time_status(s, Phase.PHASE_1).seconds_left > 3500


async def test_expired_phase_auto_submits(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    s.phase1_hard_stop = utcnow() - timedelta(seconds=1)
    await db.flush()

    assert svc.time_status(s, Phase.PHASE_1).expired
    await svc.enforce_deadline(db, s)
    assert s.status is SessionStatus.PHASE_1_COMPLETED


async def test_saving_after_the_deadline_is_refused(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    qid = uuid.UUID(s.question_order[Phase.PHASE_1.value][0])
    s.phase1_hard_stop = utcnow() - timedelta(seconds=1)
    await svc.enforce_deadline(db, s)

    with pytest.raises(ExamStateError):
        await svc.save_answer(
            db, s, qid, selected_option_ids=[], text_answer=None, is_marked_for_review=False
        )


# --------------------------------------------------------- state machine
async def test_phase1_locks_permanently_on_submit(db, candidate, exam, phase1_questions):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    await svc.submit_phase1(db, s)
    assert s.status is SessionStatus.PHASE_1_COMPLETED

    # Re-submitting is a no-op, not an error or a second scoring pass.
    await svc.submit_phase1(db, s)
    assert s.status is SessionStatus.PHASE_1_COMPLETED
    # And it cannot be restarted.
    with pytest.raises(ExamStateError):
        await svc.start_phase1(db, s, exam)


async def test_phase2_requires_phase1_then_a_domain(
    db, candidate, exam, phase1_questions, phase2_questions, domain
):
    s = await _session(db, candidate, exam)
    with pytest.raises(ExamStateError):
        await svc.start_phase2(db, s, exam)

    await svc.start_phase1(db, s, exam)
    await svc.submit_phase1(db, s)

    with pytest.raises(ExamStateError):
        await svc.start_phase2(db, s, exam)   # no domain chosen yet

    await svc.select_domain(db, s, domain.id)
    await svc.start_phase2(db, s, exam)
    assert s.status is SessionStatus.PHASE_2_IN_PROGRESS


async def test_domain_selection_is_a_one_way_door(
    db, candidate, exam, phase1_questions, phase2_questions, domain
):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    await svc.submit_phase1(db, s)
    await svc.select_domain(db, s, domain.id)
    assert s.domain_locked_at is not None

    other = type(domain)(code="MATH", name="Mathematics", is_active=True)
    db.add(other)
    await db.flush()
    with pytest.raises(ExamStateError):
        await svc.select_domain(db, s, other.id)
    assert s.selected_domain_id == domain.id


async def test_domain_with_no_questions_cannot_be_chosen(
    db, candidate, exam, phase1_questions
):
    from app.models import Domain

    empty = Domain(code="TAM", name="Tamil", is_active=True)
    db.add(empty)
    await db.flush()

    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    await svc.submit_phase1(db, s)
    with pytest.raises(ExamStateError):
        await svc.select_domain(db, s, empty.id)

    selectable = await svc.list_selectable_domains(db)
    assert empty.id not in [d.id for d, _ in selectable]


async def test_phase1_questions_never_appear_in_phase2(
    db, candidate, exam, phase1_questions, phase2_questions, domain
):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    await svc.submit_phase1(db, s)
    await svc.select_domain(db, s, domain.id)
    await svc.start_phase2(db, s, exam)

    p1 = set(s.question_order[Phase.PHASE_1.value])
    p2 = set(s.question_order[Phase.PHASE_2.value])
    assert p1
    assert p2
    assert p1.isdisjoint(p2)
    assert p2.issubset({str(q.id) for q in phase2_questions})


# ------------------------------------------------------------- answering
async def test_answer_upsert_is_idempotent(db, candidate, exam, phase1_questions):
    from sqlalchemy import func, select

    from app.models import CandidateAnswer

    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    qid = uuid.UUID(s.question_order[Phase.PHASE_1.value][0])
    q = next(x for x in phase1_questions if x.id == qid)
    a, b = q.options[0].id, q.options[1].id

    for choice in (a, b, a):
        await svc.save_answer(
            db, s, qid, selected_option_ids=[choice], text_answer=None,
            is_marked_for_review=False,
        )
    count = (
        await db.execute(
            select(func.count(CandidateAnswer.id)).where(CandidateAnswer.session_id == s.id)
        )
    ).scalar_one()
    assert count == 1

    saved = (
        await db.execute(
            select(CandidateAnswer).where(CandidateAnswer.session_id == s.id)
        )
    ).scalar_one()
    assert saved.selected_option_ids == [str(a)]


async def test_answering_a_question_outside_your_paper_is_refused(
    db, candidate, exam, phase1_questions
):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    allocated = set(s.question_order[Phase.PHASE_1.value])
    outside = next(q for q in phase1_questions if str(q.id) not in allocated)
    with pytest.raises(ExamStateError):
        await svc.save_answer(
            db, s, outside.id, selected_option_ids=[], text_answer=None,
            is_marked_for_review=False,
        )


async def test_option_ids_from_another_question_are_discarded(
    db, candidate, exam, phase1_questions
):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    order = s.question_order[Phase.PHASE_1.value]
    mine = next(q for q in phase1_questions if str(q.id) == order[0])
    theirs = next(q for q in phase1_questions if str(q.id) == order[1])

    answer = await svc.save_answer(
        db, s, mine.id, selected_option_ids=[theirs.options[0].id],
        text_answer=None, is_marked_for_review=False,
    )
    assert answer.selected_option_ids == []


async def test_single_choice_keeps_only_one_selection(
    db, candidate, exam, phase1_questions
):
    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    qid = uuid.UUID(s.question_order[Phase.PHASE_1.value][0])
    q = next(x for x in phase1_questions if x.id == qid)
    answer = await svc.save_answer(
        db, s, qid, selected_option_ids=[o.id for o in q.options],
        text_answer=None, is_marked_for_review=False,
    )
    assert len(answer.selected_option_ids) == 1


# --------------------------------------------------------------- scoring
async def test_scoring_awards_marks_and_applies_negatives(
    db, candidate, exam, phase1_questions
):
    from sqlalchemy import select

    from app.models import ExamResult

    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    order = s.question_order[Phase.PHASE_1.value]
    by_id = {str(q.id): q for q in phase1_questions}

    # 3 right, 1 wrong, 1 left blank.
    for i, qid in enumerate(order):
        q = by_id[qid]
        if i < 3:
            choice = next(o for o in q.options if o.is_correct)
        elif i == 3:
            choice = next(o for o in q.options if not o.is_correct)
        else:
            continue
        await svc.save_answer(
            db, s, q.id, selected_option_ids=[choice.id], text_answer=None,
            is_marked_for_review=False,
        )

    await svc.submit_phase1(db, s)
    result = (
        await db.execute(select(ExamResult).where(ExamResult.session_id == s.id))
    ).scalar_one()

    assert result.phase1_correct == 3
    assert result.phase1_attempted == 4
    assert result.phase1_max == 5.0
    assert result.phase1_score == pytest.approx(3 - 0.25)   # blank is not penalised


async def test_score_never_goes_below_zero(db, candidate, exam, phase1_questions):
    from sqlalchemy import select

    from app.models import ExamResult

    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    by_id = {str(q.id): q for q in phase1_questions}
    for qid in s.question_order[Phase.PHASE_1.value]:
        q = by_id[qid]
        wrong = next(o for o in q.options if not o.is_correct)
        await svc.save_answer(
            db, s, q.id, selected_option_ids=[wrong.id], text_answer=None,
            is_marked_for_review=False,
        )
    await svc.submit_phase1(db, s)
    result = (
        await db.execute(select(ExamResult).where(ExamResult.session_id == s.id))
    ).scalar_one()
    assert result.phase1_score == 0.0


async def test_phases_are_scored_independently(
    db, candidate, exam, phase1_questions, phase2_questions, domain
):
    from sqlalchemy import select

    from app.models import ExamResult

    s = await _session(db, candidate, exam)
    await svc.start_phase1(db, s, exam)
    p1 = {str(q.id): q for q in phase1_questions}
    for qid in s.question_order[Phase.PHASE_1.value]:
        q = p1[qid]
        await svc.save_answer(
            db, s, q.id, selected_option_ids=[next(o for o in q.options if o.is_correct).id],
            text_answer=None, is_marked_for_review=False,
        )
    await svc.submit_phase1(db, s)
    await svc.select_domain(db, s, domain.id)
    await svc.start_phase2(db, s, exam)
    await svc.submit_phase2(db, s)

    result = (
        await db.execute(select(ExamResult).where(ExamResult.session_id == s.id))
    ).scalar_one()
    assert result.phase1_score == 5.0
    assert result.phase2_score == 0.0      # phase 2 left entirely blank
    assert result.total_score == 5.0
    assert result.total_max == 10.0
    assert s.status is SessionStatus.COMPLETED
