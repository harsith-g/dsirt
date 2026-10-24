import enum
from datetime import datetime

# pyrefly: ignore [missing-import]
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text, Enum, Float, Index
)
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship

from backend.database import Base


class UserRole(str, enum.Enum):
    candidate = "candidate"    # exam attender
    admin = "admin"
    setter = "setter"


class SessionStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    completed = "completed"
    disqualified = "disqualified"
    expired = "expired"


class QuestionType(str, enum.Enum):
    mcq = "mcq"
    written = "written"


class ExamGlobalState(Base):
    """
    Singleton table (always exactly one row, id=1) that tracks whether the
    exam is globally open or closed, and the scheduled start/end window.

    scheduled_start_at:
        The official exam opening time (UTC). Candidates who try to start
        before this are shown a countdown. Admin must also have toggled
        exam_open = True for sessions to begin.

    scheduled_end_at:
        The global hard-stop time (UTC). Once this time is reached:
        - No new sessions may start.
        - All in-progress sessions are auto-expired.
        - A candidate who started at 10:30 AM when end is 12:00 PM gets
          only 90 minutes — not a fresh EXAM_DURATION_MINUTES.

    exam_open:
        Admin toggle. Both exam_open=True AND current time within the
        scheduled window are required for candidates to enter.
    """
    __tablename__ = "exam_global_state"

    id = Column(Integer, primary_key=True, default=1)
    exam_open = Column(Boolean, nullable=False, default=False)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)

    # Scheduled exam window (stored in UTC)
    scheduled_start_at = Column(DateTime, nullable=True)
    scheduled_end_at   = Column(DateTime, nullable=True)
    # Display timezone label (informational, for UI display only)
    timezone_label     = Column(String, nullable=False, default="Asia/Kolkata")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.candidate)
    approval_status = Column(String, nullable=False, default="registered")
    must_reset_password = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Monotonic counter, not a timestamp: every JWT embeds the value that
    # was current at issuance, and any request whose embedded value is
    # behind the user's current counter is rejected outright. Bumped on
    # disqualification so every token this candidate holds dies instantly.
    token_version = Column(Integer, nullable=False, default=0)

    sessions = relationship("ExamSession", back_populates="user")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(Integer, nullable=False)  # canonical order before shuffle
    domain = Column(String, nullable=False, index=True, default="General")
    question_type = Column(Enum(QuestionType), nullable=False)
    phase = Column(Integer, nullable=False, default=1)
    question_text = Column(Text, nullable=False)
    # For MCQ: JSON list of option strings, in canonical (unshuffled) order.
    options = Column(JSON, nullable=True)
    # For MCQ: index into `options` (canonical order) that is correct.
    # Not exposed to candidates at any point.
    correct_option = Column(Integer, nullable=True)


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(Enum(SessionStatus), nullable=False, default=SessionStatus.not_started, index=True)

    started_at = Column(DateTime, nullable=True)
    # Unified single exam timer driven by the global scheduled_end_at.
    # This is what the candidate's timer counts down to.
    exam_ends_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)

    # Phase tracking — NEW
    # current_phase: 1 = Phase 1 in progress, 2 = Phase 2 in progress
    current_phase = Column(Integer, nullable=False, default=1)
    # Timestamps when each phase was submitted by the candidate
    phase1_submitted_at = Column(DateTime, nullable=True)
    phase2_submitted_at = Column(DateTime, nullable=True)

    paper2_domain = Column(String, nullable=True)
    # domain_selected tracks whether the candidate has picked their Phase 2 domain
    domain_selected = Column(Boolean, nullable=False, default=False)
    # Combined total score (for backward-compatibility). Per-phase scores
    # are stored in CandidateResult rows.
    score = Column(Integer, nullable=True)

    # Ordered list of question IDs, shuffled once at session start.
    # Phase 2 question IDs are appended when the candidate selects a domain.
    question_order = Column(JSON, nullable=True)
    # {question_id: [shuffled option indices mapping display->canonical]}
    option_orders = Column(JSON, nullable=True)

    current_index = Column(Integer, nullable=False, default=0)
    strikes = Column(Integer, nullable=False, default=0)
    last_activity = Column(DateTime, nullable=False, default=datetime.utcnow)

    # When True, admin has granted this candidate permission to re-enter
    # after a network drop or accidental disqualification without losing
    # previously saved answers.
    retake_granted = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="sessions")
    answers = relationship("Answer", back_populates="session", cascade="all, delete-orphan")
    violations = relationship("Violation", back_populates="session", cascade="all, delete-orphan")
    results = relationship("CandidateResult", back_populates="session", cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("exam_sessions.id"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False, index=True)

    # For MCQ: the *display* index the candidate clicked (server maps back
    # to canonical option using option_orders before ever comparing/scoring).
    selected_display_index = Column(Integer, nullable=True)
    written_text = Column(Text, nullable=True)

    marked_for_review = Column(Boolean, nullable=False, default=False)
    visited = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("ExamSession", back_populates="answers")


class Violation(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("exam_sessions.id"), nullable=False, index=True)
    violation_type = Column(String, nullable=False)
    strike_number = Column(Integer, nullable=False)  # violation count AFTER this event
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    session = relationship("ExamSession", back_populates="violations")


class CandidateResult(Base):
    """
    Admin-only per-phase result record. Created server-side when a phase
    is submitted. NEVER returned to candidate endpoints.

    One row per phase per session:
      phase=1 → Research Methodology result
      phase=2 → Domain-specific result

    question_detail stores a JSON array of per-question analysis:
      [{
        "question_id": int,
        "question_text": str,
        "display_index_chosen": int | null,   # what candidate selected (display order)
        "canonical_index_chosen": int | null, # mapped back to canonical option order
        "correct_option": int,                # canonical correct index
        "is_correct": bool,
        "is_answered": bool,
        "candidate_answer_text": str | null,  # resolved option text
        "correct_answer_text": str | null,    # resolved correct option text
        "marks": int                           # 1 if correct, 0 otherwise
      }]

    This data is ADMIN-ONLY. The /exam/* endpoints must never return it.
    """
    __tablename__ = "candidate_results"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("exam_sessions.id"), nullable=False, index=True)
    phase = Column(Integer, nullable=False)          # 1 or 2
    domain = Column(String, nullable=True)           # null for phase 1
    total_questions = Column(Integer, nullable=False, default=0)
    attempted = Column(Integer, nullable=False, default=0)
    correct = Column(Integer, nullable=False, default=0)
    incorrect = Column(Integer, nullable=False, default=0)
    unanswered = Column(Integer, nullable=False, default=0)
    score = Column(Integer, nullable=False, default=0)
    max_score = Column(Integer, nullable=False, default=0)
    accuracy = Column(Float, nullable=True)          # 0.0 – 100.0
    submitted_at = Column(DateTime, nullable=True)
    question_detail = Column(JSON, nullable=True)    # per-question breakdown

    session = relationship("ExamSession", back_populates="results")
