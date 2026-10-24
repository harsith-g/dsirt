"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.enums import (
    AdminRole,
    CandidateStatus,
    Phase,
    QuestionType,
    SessionStatus,
    ViolationType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------- auth in
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


# ---------------------------------------------------------------- auth out
class CandidateOut(ORMModel):
    id: uuid.UUID
    registration_number: str
    email: str
    full_name: str
    programme: str | None = None
    status: CandidateStatus


class AdminOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: AdminRole


class LoginResponse(BaseModel):
    ok: bool = True
    redirect: str
    expires_at: datetime


# -------------------------------------------------------------------- exam
class DomainOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    question_count: int = 0


class OptionOut(BaseModel):
    id: str
    text: str


class QuestionOut(BaseModel):
    number: int
    id: str
    question_type: QuestionType
    question_text: str
    marks: float
    negative_marks: float
    options: list[OptionOut]
    selected_option_ids: list[str] = Field(default_factory=list)
    text_answer: str | None = None
    is_marked_for_review: bool = False


class PaperOut(BaseModel):
    phase: Phase
    questions: list[QuestionOut]


class SessionStateOut(BaseModel):
    session_id: uuid.UUID
    status: SessionStatus
    current_phase: Phase
    selected_domain: DomainOut | None = None
    domain_locked: bool = False
    seconds_left: int = 0
    hard_stop_time: datetime | None = None
    server_time: datetime
    violation_count: int = 0
    total_questions: int = 0
    answered: int = 0
    marked_for_review: int = 0


class AnswerSaveRequest(BaseModel):
    question_id: uuid.UUID
    selected_option_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    text_answer: str | None = Field(default=None, max_length=20000)
    is_marked_for_review: bool = False


class AnswerSaveResponse(BaseModel):
    ok: bool = True
    question_id: uuid.UUID
    saved_at: datetime
    seconds_left: int
    status: SessionStatus


class DomainSelectRequest(BaseModel):
    domain_id: uuid.UUID


class SubmitResponse(BaseModel):
    ok: bool = True
    status: SessionStatus
    next_step: str
    message: str


class ResultOut(BaseModel):
    phase1_score: float
    phase1_max: float
    phase1_attempted: int
    phase1_correct: int
    phase2_score: float
    phase2_max: float
    phase2_attempted: int
    phase2_correct: int
    total_score: float
    total_max: float
    is_published: bool


# -------------------------------------------------------------- proctoring
class ViolationCreate(BaseModel):
    violation_type: ViolationType
    details: str | None = Field(default=None, max_length=2000)
    # data: URL for the webcam snapshot captured at the moment of the event.
    snapshot_data_url: str | None = Field(default=None, max_length=4_000_000)


class ViolationOut(ORMModel):
    id: uuid.UUID
    violation_type: ViolationType
    details: str | None
    snapshot_url: str | None
    timestamp: datetime


class HeartbeatRequest(BaseModel):
    ping_ms: int | None = Field(default=None, ge=0, le=60000)
    camera_active: bool = True
    fullscreen: bool = True


# ------------------------------------------------------------------- admin
class LiveSessionOut(BaseModel):
    session_id: uuid.UUID
    candidate_id: uuid.UUID
    registration_number: str
    full_name: str
    status: SessionStatus
    current_phase: Phase
    domain: str | None = None
    seconds_left: int = 0
    violation_count: int = 0
    ping_ms: int | None = None
    camera_active: bool = True
    online: bool = False
    last_seen: datetime | None = None


class AdminDashboardOut(BaseModel):
    candidates_total: int
    candidates_approved: int
    sessions_in_progress: int
    sessions_completed: int
    questions_phase1: int
    questions_phase2: int
    violations_today: int
    online_now: int
    db_ok: bool
    redis_ok: bool


class ImportResultOut(BaseModel):
    ok: bool
    total_rows: int
    imported: int
    duplicates_in_db: int
    duplicates_in_file: int
    errors: list[dict]
    warnings: list[str]


class CandidateStatusUpdate(BaseModel):
    status: CandidateStatus
    reason: str | None = Field(default=None, max_length=500)


class MessageOut(BaseModel):
    ok: bool = True
    message: str
