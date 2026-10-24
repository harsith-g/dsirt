"""Initial schema: all 11 entities, enums, constraints and indexes.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    # create_type=False: the types are created explicitly in upgrade() so the
    # ordering is deterministic and a re-run does not race.
    return postgresql.ENUM(*values, name=name, create_type=False)


ENUMS = {
    "admin_role": ("SUPER_ADMIN", "ADMIN", "PROCTOR"),
    "candidate_status": ("REGISTERED", "APPROVED", "ACTIVE", "BLOCKED", "DISQUALIFIED"),
    "question_phase": ("PHASE_1", "PHASE_2"),
    "question_type": ("SINGLE_CHOICE", "MULTIPLE_CHOICE", "TRUE_FALSE", "TEXT"),
    "question_difficulty": ("EASY", "MEDIUM", "HARD"),
    "session_status": (
        "NOT_STARTED", "PHASE_1_IN_PROGRESS", "PHASE_1_COMPLETED",
        "PHASE_2_IN_PROGRESS", "COMPLETED", "TERMINATED",
    ),
    "violation_type": (
        "TAB_SWITCH", "FULLSCREEN_EXIT", "CAMERA_DISCONNECTED", "MULTIPLE_FACES",
        "NO_FACE", "WINDOW_BLUR", "COPY_PASTE", "DEVTOOLS", "NETWORK_LOST",
    ),
    "actor_type": ("ADMIN", "CANDIDATE", "SYSTEM"),
}

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    bind = op.get_bind()
    # gen_random_uuid() is built in from PG13; pgcrypto covers older servers.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    def pk():
        return sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        )

    def stamps():
        return (
            sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        )

    # ------------------------------------------------------------- admins
    op.create_table(
        "admins",
        pk(),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("role", _enum("admin_role"), nullable=False, server_default="ADMIN"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", TS),
        *stamps(),
    )
    op.create_index("ix_admins_email", "admins", ["email"])

    # --------------------------------------------------------- candidates
    op.create_table(
        "candidates",
        pk(),
        sa.Column("registration_number", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32)),
        sa.Column("programme", sa.String(255)),
        sa.Column(
            "status", _enum("candidate_status"), nullable=False, server_default="REGISTERED"
        ),
        sa.Column("last_login_at", TS),
        *stamps(),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"])
    op.create_index("ix_candidates_registration_number", "candidates", ["registration_number"])
    op.create_index("ix_candidates_status", "candidates", ["status"])

    # ------------------------------------------------------------ domains
    op.create_table(
        "domains",
        pk(),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *stamps(),
    )
    op.create_index("ix_domains_code", "domains", ["code"])
    op.create_index("ix_domains_is_active", "domains", ["is_active"])

    # -------------------------------------------------------------- exams
    op.create_table(
        "exams",
        pk(),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("phase1_duration_mins", sa.Integer, nullable=False, server_default="60"),
        sa.Column("phase2_duration_mins", sa.Integer, nullable=False, server_default="60"),
        sa.Column("phase1_question_count", sa.Integer, nullable=False, server_default="50"),
        sa.Column("phase2_question_count", sa.Integer, nullable=False, server_default="50"),
        sa.Column("start_time", TS),
        sa.Column("hard_stop_time", TS),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *stamps(),
        sa.CheckConstraint("phase1_duration_mins > 0", name="ck_exam_phase1_duration_positive"),
        sa.CheckConstraint("phase2_duration_mins > 0", name="ck_exam_phase2_duration_positive"),
        sa.CheckConstraint(
            "start_time IS NULL OR hard_stop_time IS NULL OR hard_stop_time > start_time",
            name="ck_exam_window_ordered",
        ),
    )
    op.create_index("ix_exams_is_active", "exams", ["is_active"])

    # ---------------------------------------------------------- questions
    op.create_table(
        "questions",
        pk(),
        sa.Column("phase", _enum("question_phase"), nullable=False),
        sa.Column(
            "domain_id", UUID, sa.ForeignKey("domains.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column(
            "question_type", _enum("question_type"), nullable=False,
            server_default="SINGLE_CHOICE",
        ),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("question_hash", sa.String(32), nullable=False),
        sa.Column("marks", sa.Float, nullable=False, server_default="1"),
        sa.Column("negative_marks", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "difficulty", _enum("question_difficulty"), nullable=False, server_default="MEDIUM"
        ),
        sa.Column("explanation", sa.Text),
        sa.Column("source_ref", sa.String(255)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *stamps(),
        sa.UniqueConstraint("question_hash", "phase", "domain_id", name="uq_question_hash_scope"),
        sa.CheckConstraint(
            "(phase = 'PHASE_1' AND domain_id IS NULL) OR "
            "(phase = 'PHASE_2' AND domain_id IS NOT NULL)",
            name="ck_question_phase_domain_consistency",
        ),
        sa.CheckConstraint("marks > 0", name="ck_question_marks_positive"),
        sa.CheckConstraint(
            "negative_marks >= 0", name="ck_question_negative_marks_non_negative"
        ),
    )
    op.create_index("ix_questions_phase", "questions", ["phase"])
    op.create_index("ix_questions_domain_id", "questions", ["domain_id"])
    op.create_index("ix_questions_question_hash", "questions", ["question_hash"])
    op.create_index("ix_questions_is_active", "questions", ["is_active"])
    op.create_index(
        "ix_questions_phase_domain_active", "questions", ["phase", "domain_id", "is_active"]
    )
    # NULLs are distinct in a UNIQUE constraint, so Phase 1 duplicates would
    # slip past uq_question_hash_scope. This partial index catches them.
    op.create_index(
        "uq_question_hash_phase1",
        "questions",
        ["question_hash"],
        unique=True,
        postgresql_where=sa.text("domain_id IS NULL"),
    )

    # --------------------------------------------------- question_options
    op.create_table(
        "question_options",
        pk(),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("option_label", sa.String(8), nullable=False),
        sa.Column("option_text", sa.Text, nullable=False),
        sa.Column("is_correct", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("question_id", "option_label", name="uq_option_label_per_question"),
    )
    op.create_index("ix_question_options_question_id", "question_options", ["question_id"])

    # ------------------------------------------------------ exam_sessions
    op.create_table(
        "exam_sessions",
        pk(),
        sa.Column(
            "candidate_id", UUID, sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exam_id", UUID, sa.ForeignKey("exams.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "current_phase", _enum("question_phase"), nullable=False, server_default="PHASE_1"
        ),
        sa.Column(
            "selected_domain_id", UUID, sa.ForeignKey("domains.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("domain_locked_at", TS),
        sa.Column("phase1_start", TS),
        sa.Column("phase1_hard_stop", TS),
        sa.Column("phase1_end", TS),
        sa.Column("phase2_start", TS),
        sa.Column("phase2_hard_stop", TS),
        sa.Column("phase2_end", TS),
        sa.Column(
            "status", _enum("session_status"), nullable=False, server_default="NOT_STARTED"
        ),
        sa.Column(
            "question_order", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "option_order", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.Text),
        sa.Column("violation_count", sa.Integer, nullable=False, server_default="0"),
        *stamps(),
        sa.UniqueConstraint("candidate_id", "exam_id", name="uq_session_per_candidate_exam"),
    )
    op.create_index("ix_exam_sessions_candidate_id", "exam_sessions", ["candidate_id"])
    op.create_index("ix_exam_sessions_exam_id", "exam_sessions", ["exam_id"])
    op.create_index("ix_exam_sessions_selected_domain_id", "exam_sessions", ["selected_domain_id"])
    op.create_index("ix_exam_sessions_status", "exam_sessions", ["status"])
    op.create_index("ix_sessions_status_phase", "exam_sessions", ["status", "current_phase"])

    # --------------------------------------------------- candidate_answers
    op.create_table(
        "candidate_answers",
        pk(),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("exam_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", _enum("question_phase"), nullable=False),
        sa.Column(
            "selected_option_ids", postgresql.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("text_answer", sa.Text),
        sa.Column("is_marked_for_review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_correct", sa.Boolean),
        sa.Column("awarded_marks", sa.Float),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "question_id", name="uq_answer_per_session_question"),
    )
    op.create_index("ix_candidate_answers_session_id", "candidate_answers", ["session_id"])
    op.create_index("ix_candidate_answers_question_id", "candidate_answers", ["question_id"])
    op.create_index("ix_candidate_answers_phase", "candidate_answers", ["phase"])

    # ------------------------------------------------------- exam_results
    op.create_table(
        "exam_results",
        pk(),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("exam_sessions.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("phase1_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("phase1_max", sa.Float, nullable=False, server_default="0"),
        sa.Column("phase1_attempted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase1_correct", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase2_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("phase2_max", sa.Float, nullable=False, server_default="0"),
        sa.Column("phase2_attempted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase2_correct", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_max", sa.Float, nullable=False, server_default="0"),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("published_at", TS),
        *stamps(),
    )
    op.create_index("ix_exam_results_session_id", "exam_results", ["session_id"])
    op.create_index("ix_exam_results_is_published", "exam_results", ["is_published"])

    # --------------------------------------------------------- violations
    op.create_table(
        "violations",
        pk(),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("exam_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id", UUID, sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("violation_type", _enum("violation_type"), nullable=False),
        sa.Column("details", sa.Text),
        sa.Column("snapshot_url", sa.String(512)),
        sa.Column("timestamp", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_violations_session_id", "violations", ["session_id"])
    op.create_index("ix_violations_candidate_id", "violations", ["candidate_id"])
    op.create_index("ix_violations_violation_type", "violations", ["violation_type"])
    op.create_index("ix_violations_timestamp", "violations", ["timestamp"])
    op.create_index("ix_violations_session_time", "violations", ["session_id", "timestamp"])

    # --------------------------------------------------------- audit_logs
    op.create_table(
        "audit_logs",
        pk(),
        sa.Column("actor_type", _enum("actor_type"), nullable=False),
        sa.Column("actor_id", UUID),
        sa.Column("actor_label", sa.String(255)),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.Text),
        sa.Column("timestamp", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_actor_type", "audit_logs", ["actor_type"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])


def downgrade() -> None:
    for table in (
        "audit_logs", "violations", "exam_results", "candidate_answers",
        "exam_sessions", "question_options", "questions", "exams",
        "domains", "candidates", "admins",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for name in ENUMS:
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
