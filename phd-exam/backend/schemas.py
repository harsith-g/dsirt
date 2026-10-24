from typing import Optional, List
from datetime import datetime
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, EmailStr, Field, field_validator

from backend.config import DOMAINS


# ---------- Auth ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    must_reset_password: bool
    full_name: str


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


# ---------- Global Exam State ----------
class GlobalExamStateOut(BaseModel):
    exam_open: bool
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    scheduled_end_at: Optional[datetime] = None
    timezone_label: str = "Asia/Kolkata"


class ExamScheduleRequest(BaseModel):
    """Admin sets the scheduled exam window (UTC timestamps)."""
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    timezone_label: str = "Asia/Kolkata"


# ---------- Exam: candidate-facing ----------
class QuestionOut(BaseModel):
    index: int              # position within this candidate's shuffled exam (0-based)
    total: int
    phase: int               # 1 = Research Methodology, 2 = Domain Specific
    phase_label: str
    question_type: str
    question_text: str
    options: Optional[List[str]] = None   # already shuffled for display; None for written
    selected_display_index: Optional[int] = None
    written_text: Optional[str] = None
    marked_for_review: bool = False


class ExamStateOut(BaseModel):
    status: str
    exam_open: bool
    domain_selected: bool
    paper2_domain: Optional[str] = None
    time_remaining_seconds: int
    current_index: int
    total_questions: int
    phase1_count: int
    strikes: int
    max_strikes: int
    nav: List[dict]   # [{index, phase, answered, marked_for_review, visited}]
    # Phase tracking — safe to expose (no scores, no answers)
    current_phase: int = 1
    phase1_submitted: bool = False
    phase1_submitted_at: Optional[datetime] = None
    # Scheduled window (for candidate countdown display)
    scheduled_start_at: Optional[datetime] = None
    scheduled_end_at: Optional[datetime] = None


class CandidateDashboardOut(BaseModel):
    """Candidate dashboard data — no scores, no results."""
    full_name: str
    email: str
    exam_open: bool
    scheduled_start_at: Optional[datetime] = None
    scheduled_end_at: Optional[datetime] = None
    timezone_label: str = "Asia/Kolkata"
    # Time until scheduled start (seconds). 0 if already started or no schedule.
    seconds_until_start: int = 0
    # Session info
    session_status: Optional[str] = None  # None = no session yet
    current_phase: int = 1
    phase1_status: str = "not_started"     # not_started | in_progress | completed
    phase2_status: str = "locked"          # locked | ready | in_progress | completed
    phase1_submitted_at: Optional[datetime] = None
    phase2_submitted_at: Optional[datetime] = None
    time_remaining_seconds: int = 0
    # Whether the candidate is currently in an in-progress session
    has_active_session: bool = False


class AnswerSubmit(BaseModel):
    index: int
    selected_display_index: Optional[int] = None
    written_text: Optional[str] = None
    marked_for_review: Optional[bool] = None


class SelectDomainRequest(BaseModel):
    domain: str


class NavigateRequest(BaseModel):
    direction: Optional[str] = None   # "prev" | "next"
    jump_to: Optional[int] = None


class ViolationReport(BaseModel):
    violation_type: str


class ViolationResult(BaseModel):
    strikes: int
    max_strikes: int
    action: str        # always "warn" now — "disqualified" only via admin endpoint
    violation_type: str


class CameraStatusReport(BaseModel):
    """
    Informational camera-health ping from the candidate client — NOT a
    violation. Sent on getUserMedia() success/failure so the admin grid can
    show "Camera Active" / "Camera Blocked" without penalising a candidate
    strikes-wise for a hardware/permission problem that isn't their fault.
    """
    camera_status: str          # "active" | "blocked"
    reason: Optional[str] = None  # e.g. "NotAllowedError", "NotFoundError"


class CameraStatusResult(BaseModel):
    ok: bool = True


# ---------- Admin ----------
class CandidateStatusOut(BaseModel):
    session_id: int
    user_id: int
    full_name: str
    email: str
    status: str
    strikes: int
    max_strikes: int
    answered_count: int
    total_questions: int
    time_remaining_seconds: int
    last_activity_seconds_ago: int
    score: Optional[int] = None
    domain_selected: bool = False
    paper2_domain: Optional[str] = None
    current_phase: int = 1
    phase1_submitted_at: Optional[datetime] = None
    phase2_submitted_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None


# ---------- Admin: per-question result ----------
class QuestionResultItem(BaseModel):
    question_id: int
    question_text: str
    display_index_chosen: Optional[int] = None
    canonical_index_chosen: Optional[int] = None
    correct_option: Optional[int] = None
    is_correct: bool
    is_answered: bool
    candidate_answer_text: Optional[str] = None
    correct_answer_text: Optional[str] = None
    marks: int


class PhaseResultOut(BaseModel):
    phase: int
    domain: Optional[str] = None
    total_questions: int
    attempted: int
    correct: int
    incorrect: int
    unanswered: int
    score: int
    max_score: int
    accuracy: Optional[float] = None
    submitted_at: Optional[datetime] = None
    question_detail: Optional[List[QuestionResultItem]] = None


class CandidateFullResultOut(BaseModel):
    """Admin-only full result for a candidate."""
    user_id: int
    full_name: str
    email: str
    session_id: int
    session_status: str
    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    domain: Optional[str] = None
    phase1: Optional[PhaseResultOut] = None
    phase2: Optional[PhaseResultOut] = None
    # Combined totals
    total_questions: int = 0
    total_attempted: int = 0
    total_correct: int = 0
    total_incorrect: int = 0
    total_unanswered: int = 0
    total_score: int = 0
    total_max_score: int = 0
    overall_accuracy: Optional[float] = None
    violations: Optional[List[dict]] = None


# ---------- Admin: violation detail ----------
class ViolationDetailOut(BaseModel):
    id: int
    violation_type: str
    strike_number: int
    created_at: datetime


class DisqualifyRequest(BaseModel):
    reason: Optional[str] = None


class DisqualifyResponse(BaseModel):
    user_id: int
    email: str
    message: str


# ---------- Admin: staff & candidate account management ----------
class ManagedUserOut(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    is_active: bool
    must_reset_password: bool
    # Only meaningful for candidates
    latest_session_status: Optional[str] = None
    latest_session_score: Optional[int] = None


class CreateManagedUserRequest(BaseModel):
    full_name: str = Field(min_length=1)
    email: EmailStr
    role: str  # "setter" or "candidate" — admin accounts are not created here

    @field_validator("role")
    @classmethod
    def role_must_be_manageable(cls, v: str) -> str:
        if v not in ("setter", "candidate"):
            raise ValueError('role must be "setter" or "candidate"')
        return v


class CreateManagedUserResponse(BaseModel):
    user: ManagedUserOut
    temporary_password: str


class UpdateManagedUserRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class AdminResetPasswordResponse(BaseModel):
    user_id: int
    email: str
    temporary_password: str


class ReactivateCandidateResponse(BaseModel):
    user_id: int
    email: str
    message: str


class GrantRetakeResponse(BaseModel):
    user_id: int
    email: str
    message: str


# ---------- Admin / setter: question bank ----------
class QuestionIn(BaseModel):
    order_no: int
    domain: str
    question_type: str  # "mcq" | "written"
    question_text: str = Field(min_length=1)
    options: Optional[List[str]] = None
    correct_option: Optional[int] = None

    @field_validator("question_type")
    @classmethod
    def valid_question_type(cls, v: str) -> str:
        if v not in ("mcq", "written"):
            raise ValueError('question_type must be "mcq" or "written"')
        return v

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, v: str) -> str:
        if v not in DOMAINS:
            raise ValueError(f"domain must be one of: {', '.join(DOMAINS)}")
        return v


class QuestionAdminOut(BaseModel):
    id: int
    order_no: int
    domain: str
    question_type: str
    question_text: str
    options: Optional[List[str]] = None
    correct_option: Optional[int] = None


class BulkImportRowError(BaseModel):
    row: int  # 1-based
    message: str


class BulkImportResult(BaseModel):
    created: int
    errors: List[BulkImportRowError]


class QuestionBankMeta(BaseModel):
    domains: List[str]
    question_types: List[str] = ["mcq", "written"]
