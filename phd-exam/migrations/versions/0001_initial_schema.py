"""Initial schema — baseline for the exam platform.

This mirrors exactly what `Base.metadata.create_all()` used to build at
app startup (same tables, columns, nullability, indexes, FKs — no
server-side defaults, since the ORM applies its `default=` values at
insert time, same as create_all always did). From here on, every schema
change is a new Alembic revision instead of an implicit create_all().

If you are adopting this on a database that an earlier, pre-Alembic
version of this app already created (via create_all() or backend/migrate.py),
do NOT run `alembic upgrade head` — it will try to create tables that
already exist and fail. Instead run `alembic stamp head` once to tell
Alembic "the schema is already at this point", then use `alembic upgrade
head` for every migration after this one. See deploy/README.md.

Revision ID: 0001
Revises:
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from backend.models import UserRole, SessionStatus, QuestionType

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exam_global_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_open", sa.Boolean(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(), nullable=True),
        sa.Column("timezone_label", sa.String(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("role", sa.Enum(UserRole), nullable=False),
        sa.Column("approval_status", sa.String(), nullable=False),
        sa.Column("must_reset_password", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("question_type", sa.Enum(QuestionType), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correct_option", sa.Integer(), nullable=True),
    )
    op.create_index("ix_questions_id", "questions", ["id"])
    op.create_index("ix_questions_domain", "questions", ["domain"])
    op.create_index("ix_questions_phase", "questions", ["phase"])

    op.create_table(
        "exam_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Enum(SessionStatus), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("exam_ends_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("current_phase", sa.Integer(), nullable=False),
        sa.Column("phase1_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("phase2_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("paper2_domain", sa.String(), nullable=True),
        sa.Column("domain_selected", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("question_order", sa.JSON(), nullable=True),
        sa.Column("option_orders", sa.JSON(), nullable=True),
        sa.Column("current_index", sa.Integer(), nullable=False),
        sa.Column("strikes", sa.Integer(), nullable=False),
        sa.Column("last_activity", sa.DateTime(), nullable=False),
        sa.Column("retake_granted", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_exam_sessions_id", "exam_sessions", ["id"])
    op.create_index("ix_exam_sessions_user_id", "exam_sessions", ["user_id"])
    op.create_index("ix_exam_sessions_status", "exam_sessions", ["status"])

    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("exam_sessions.id"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("selected_display_index", sa.Integer(), nullable=True),
        sa.Column("written_text", sa.Text(), nullable=True),
        sa.Column("marked_for_review", sa.Boolean(), nullable=False),
        sa.Column("visited", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_answers_id", "answers", ["id"])
    op.create_index("ix_answers_session_id", "answers", ["session_id"])
    op.create_index("ix_answers_question_id", "answers", ["question_id"])

    op.create_table(
        "violations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("exam_sessions.id"), nullable=False),
        sa.Column("violation_type", sa.String(), nullable=False),
        sa.Column("strike_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_violations_id", "violations", ["id"])
    op.create_index("ix_violations_session_id", "violations", ["session_id"])
    op.create_index("ix_violations_created_at", "violations", ["created_at"])

    op.create_table(
        "candidate_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("exam_sessions.id"), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("attempted", sa.Integer(), nullable=False),
        sa.Column("correct", sa.Integer(), nullable=False),
        sa.Column("incorrect", sa.Integer(), nullable=False),
        sa.Column("unanswered", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("question_detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_candidate_results_id", "candidate_results", ["id"])
    op.create_index("ix_candidate_results_session_id", "candidate_results", ["session_id"])


def downgrade() -> None:
    op.drop_table("candidate_results")
    op.drop_table("violations")
    op.drop_table("answers")
    op.drop_table("exam_sessions")
    op.drop_table("questions")
    op.drop_table("users")
    op.drop_table("exam_global_state")

    # Native Postgres enum types created implicitly by create_table() above
    # aren't dropped by drop_table() — clean them up explicitly so a
    # downgrade+upgrade cycle doesn't hit "type already exists".
    bind = op.get_bind()
    sa.Enum(UserRole).drop(bind, checkfirst=True)
    sa.Enum(SessionStatus).drop(bind, checkfirst=True)
    sa.Enum(QuestionType).drop(bind, checkfirst=True)
