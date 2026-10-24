"""SQLAlchemy 2.0 declarative models for the examination portal."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


from app.enums import (
    ActorType,
    AdminRole,
    CandidateStatus,
    Difficulty,
    Phase,
    QuestionType,
    SessionStatus,
    ViolationType,
)


def _enum(e, name):
    """Native PG enum, values (not python names) stored."""
    return SAEnum(e, name=name, values_callable=lambda x: [i.value for i in x])


def _uuid_pk():
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# -------------------------------------------------------------------- tables
class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[AdminRole] = mapped_column(
        _enum(AdminRole, "admin_role"), nullable=False, default=AdminRole.ADMIN
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Admin {self.email} {self.role}>"


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    registration_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    programme: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[CandidateStatus] = mapped_column(
        _enum(CandidateStatus, "candidate_status"),
        nullable=False,
        default=CandidateStatus.REGISTERED,
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["ExamSession"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    violations: Mapped[list["Violation"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )

    @property
    def can_login(self) -> bool:
        return self.status in (
            CandidateStatus.APPROVED,
            CandidateStatus.ACTIVE,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Candidate {self.registration_number} {self.email}>"


class Domain(Base, TimestampMixin):
    __tablename__ = "domains"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    questions: Mapped[list["Question"]] = relationship(back_populates="domain")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Domain {self.code}>"


class Exam(Base, TimestampMixin):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    phase1_duration_mins: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    phase2_duration_mins: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    phase1_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    phase2_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    # Window during which the exam may be attempted at all.
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hard_stop_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    __table_args__ = (
        CheckConstraint("phase1_duration_mins > 0", name="ck_exam_phase1_duration_positive"),
        CheckConstraint("phase2_duration_mins > 0", name="ck_exam_phase2_duration_positive"),
        CheckConstraint(
            "start_time IS NULL OR hard_stop_time IS NULL OR hard_stop_time > start_time",
            name="ck_exam_window_ordered",
        ),
    )


class Question(Base, TimestampMixin):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    phase: Mapped[Phase] = mapped_column(_enum(Phase, "question_phase"), nullable=False, index=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("domains.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    question_type: Mapped[QuestionType] = mapped_column(
        _enum(QuestionType, "question_type"), nullable=False, default=QuestionType.SINGLE_CHOICE
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    # MD5 of the normalised question text -- used to reject duplicate re-imports.
    question_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    marks: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    negative_marks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    difficulty: Mapped[Difficulty] = mapped_column(
        _enum(Difficulty, "question_difficulty"), nullable=False, default=Difficulty.MEDIUM
    )
    explanation: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    domain: Mapped["Domain | None"] = relationship(back_populates="questions")
    options: Mapped[list["QuestionOption"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.display_order",
    )

    __table_args__ = (
        # A question text may legitimately recur across *different* domains but
        # never twice inside the same phase+domain bucket. NULL domain_id for
        # phase 1 is handled by the partial index below.
        UniqueConstraint("question_hash", "phase", "domain_id", name="uq_question_hash_scope"),
        CheckConstraint(
            "(phase = 'PHASE_1' AND domain_id IS NULL) OR (phase = 'PHASE_2' AND domain_id IS NOT NULL)",
            name="ck_question_phase_domain_consistency",
        ),
        CheckConstraint("marks > 0", name="ck_question_marks_positive"),
        CheckConstraint("negative_marks >= 0", name="ck_question_negative_marks_non_negative"),
        Index("ix_questions_phase_domain_active", "phase", "domain_id", "is_active"),
        # Postgres treats NULLs as distinct in a UNIQUE constraint, so the
        # composite constraint above cannot catch duplicate Phase 1 questions
        # (whose domain_id is always NULL). This partial index closes that gap.
        Index(
            "uq_question_hash_phase1",
            "question_hash",
            unique=True,
            postgresql_where=text("domain_id IS NULL"),
        ),
    )


class QuestionOption(Base):
    __tablename__ = "question_options"

    id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    option_label: Mapped[str] = mapped_column(String(8), nullable=False)  # A/B/C/D
    option_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped["Question"] = relationship(back_populates="options")

    __table_args__ = (
        UniqueConstraint("question_id", "option_label", name="uq_option_label_per_question"),
    )


class ExamSession(Base, TimestampMixin):
    __tablename__ = "exam_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    current_phase: Mapped[Phase] = mapped_column(
        _enum(Phase, "question_phase"), nullable=False, default=Phase.PHASE_1
    )
    selected_domain_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("domains.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    domain_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    phase1_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase1_hard_stop: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase1_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase2_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase2_hard_stop: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase2_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "session_status"),
        nullable=False,
        default=SessionStatus.NOT_STARTED,
        index=True,
    )
    # Frozen, per-candidate randomised question order: {"PHASE_1": [qid,...]}
    question_order: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Frozen per-question option order so a reload never reshuffles the paper.
    option_order: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    candidate: Mapped["Candidate"] = relationship(back_populates="sessions")
    exam: Mapped["Exam"] = relationship()
    selected_domain: Mapped["Domain | None"] = relationship()
    answers: Mapped[list["CandidateAnswer"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    result: Mapped["ExamResult | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )
    violations: Mapped[list["Violation"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("candidate_id", "exam_id", name="uq_session_per_candidate_exam"),
        Index("ix_sessions_status_phase", "status", "current_phase"),
    )


class CandidateAnswer(Base):
    __tablename__ = "candidate_answers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase: Mapped[Phase] = mapped_column(_enum(Phase, "question_phase"), nullable=False, index=True)
    selected_option_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    text_answer: Mapped[str | None] = mapped_column(Text)
    is_marked_for_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Filled in at submission time by the scoring service.
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    awarded_marks: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped["ExamSession"] = relationship(back_populates="answers")
    question: Mapped["Question"] = relationship()

    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_answer_per_session_question"),
    )


class ExamResult(Base, TimestampMixin):
    __tablename__ = "exam_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    phase1_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phase1_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phase1_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase1_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase2_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phase2_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phase2_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase2_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped["ExamSession"] = relationship(back_populates="result")


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    violation_type: Mapped[ViolationType] = mapped_column(
        _enum(ViolationType, "violation_type"), nullable=False, index=True
    )
    details: Mapped[str | None] = mapped_column(Text)
    snapshot_url: Mapped[str | None] = mapped_column(String(512))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    session: Mapped["ExamSession"] = relationship(back_populates="violations")
    candidate: Mapped["Candidate"] = relationship(back_populates="violations")

    __table_args__ = (Index("ix_violations_session_time", "session_id", "timestamp"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType, "actor_type"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_label: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


__all__ = [
    "Admin",
    "AdminRole",
    "ActorType",
    "AuditLog",
    "Candidate",
    "CandidateAnswer",
    "CandidateStatus",
    "Difficulty",
    "Domain",
    "Exam",
    "ExamResult",
    "ExamSession",
    "Phase",
    "Question",
    "QuestionOption",
    "QuestionType",
    "SessionStatus",
    "Violation",
    "ViolationType",
]
