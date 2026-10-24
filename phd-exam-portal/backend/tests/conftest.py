"""Shared fixtures. Integration tests need Postgres and Redis running."""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

os.environ.setdefault("ENVIRONMENT", "development")

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.enums import CandidateStatus, Difficulty, Phase, QuestionType  # noqa: E402
from app.models import (  # noqa: E402
    Candidate,
    Domain,
    Exam,
    Question,
    QuestionOption,
)
from app.services.text_utils import question_md5  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    # A throwaway schema per run keeps tests off the live tables.
    eng = create_async_engine(settings.database_url, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def exam(db) -> Exam:
    row = Exam(
        title="Test Exam",
        phase1_duration_mins=60,
        phase2_duration_mins=60,
        phase1_question_count=5,
        phase2_question_count=5,
        is_active=True,
    )
    db.add(row)
    # commit, not flush: the ASGI test client runs its own session and would
    # not see a row that is still only in this transaction.
    await db.commit()
    return row


@pytest_asyncio.fixture
async def domain(db) -> Domain:
    row = Domain(code="CS", name="Computer Science", is_active=True)
    db.add(row)
    # commit, not flush: the ASGI test client runs its own session and would
    # not see a row that is still only in this transaction.
    await db.commit()
    return row


@pytest_asyncio.fixture
async def candidate(db) -> Candidate:
    row = Candidate(
        registration_number="TEST0001",
        email="candidate@example.com",
        hashed_password=hash_password("dsirt@123"),
        full_name="Test Candidate",
        status=CandidateStatus.APPROVED,
    )
    db.add(row)
    # commit, not flush: the ASGI test client runs its own session and would
    # not see a row that is still only in this transaction.
    await db.commit()
    return row


async def make_questions(db, *, phase: Phase, domain_id=None, count: int = 6, tag: str = "q"):
    """Four options each; option B is always the correct one."""
    made = []
    for i in range(count):
        text = f"{tag} question {i} {uuid.uuid4().hex[:6]}"
        q = Question(
            phase=phase,
            domain_id=domain_id,
            question_type=QuestionType.SINGLE_CHOICE,
            question_text=text,
            question_hash=question_md5(text),
            marks=1.0,
            negative_marks=0.25,
            difficulty=Difficulty.MEDIUM,
            is_active=True,
        )
        db.add(q)
        await db.flush()
        for j, label in enumerate("ABCD"):
            db.add(
                QuestionOption(
                    question_id=q.id,
                    option_label=label,
                    option_text=f"{label} option for {i}",
                    is_correct=(label == "B"),
                    display_order=j,
                )
            )
        made.append(q)
    await db.commit()
    return made


@pytest_asyncio.fixture
async def phase1_questions(db):
    return await make_questions(db, phase=Phase.PHASE_1, count=6, tag="p1")


@pytest_asyncio.fixture
async def phase2_questions(db, domain):
    return await make_questions(
        db, phase=Phase.PHASE_2, domain_id=domain.id, count=6, tag="p2"
    )
