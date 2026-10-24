"""
Safe, additive database migration script.

Adds new columns to existing tables and creates the new candidate_results table.
Does NOT drop any existing columns or tables.
Run ONCE before deploying the updated application.

Usage:
    python -m backend.migrate
    OR
    python backend/migrate.py

Works with both SQLite (development) and PostgreSQL (production).
"""
import logging
from sqlalchemy import text, inspect

from backend.database import engine, Base
from backend import models  # noqa — import ensures all models are registered

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def column_exists(conn, table: str, column: str) -> bool:
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def table_exists(conn, table: str) -> bool:
    insp = inspect(engine)
    return table in insp.get_table_names()


def run_migration():
    log.info("Starting database migration …")

    is_sqlite = engine.dialect.name == "sqlite"
    is_pg     = engine.dialect.name == "postgresql"

    with engine.connect() as conn:

        # ------------------------------------------------------------------ #
        # 1. exam_global_state — add scheduling columns
        # ------------------------------------------------------------------ #
        if table_exists(conn, "exam_global_state"):
            for col_name, col_def in [
                ("scheduled_start_at", "TIMESTAMP"),
                ("scheduled_end_at",   "TIMESTAMP"),
                ("timezone_label",     "VARCHAR(64) DEFAULT 'Asia/Kolkata'"),
            ]:
                if not column_exists(conn, "exam_global_state", col_name):
                    conn.execute(text(
                        f"ALTER TABLE exam_global_state ADD COLUMN {col_name} {col_def}"
                    ))
                    log.info(f"  Added exam_global_state.{col_name}")
                else:
                    log.info(f"  Skipped (exists): exam_global_state.{col_name}")
        else:
            log.info("  exam_global_state not found — will be created by SQLAlchemy")

        # ------------------------------------------------------------------ #
        # 2. exam_sessions — add phase tracking columns
        # ------------------------------------------------------------------ #
        if table_exists(conn, "exam_sessions"):
            for col_name, col_def in [
                ("current_phase",      "INTEGER DEFAULT 1"),
                ("phase1_submitted_at","TIMESTAMP"),
                ("phase2_submitted_at","TIMESTAMP"),
            ]:
                if not column_exists(conn, "exam_sessions", col_name):
                    conn.execute(text(
                        f"ALTER TABLE exam_sessions ADD COLUMN {col_name} {col_def}"
                    ))
                    log.info(f"  Added exam_sessions.{col_name}")
                else:
                    log.info(f"  Skipped (exists): exam_sessions.{col_name}")
        else:
            log.info("  exam_sessions not found — will be created by SQLAlchemy")

        # ------------------------------------------------------------------ #
        # 3. Create candidate_results table if it doesn't exist
        # ------------------------------------------------------------------ #
        if not table_exists(conn, "candidate_results"):
            conn.execute(text("""
                CREATE TABLE candidate_results (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      INTEGER NOT NULL REFERENCES exam_sessions(id),
                    phase           INTEGER NOT NULL,
                    domain          VARCHAR,
                    total_questions INTEGER NOT NULL DEFAULT 0,
                    attempted       INTEGER NOT NULL DEFAULT 0,
                    correct         INTEGER NOT NULL DEFAULT 0,
                    incorrect       INTEGER NOT NULL DEFAULT 0,
                    unanswered      INTEGER NOT NULL DEFAULT 0,
                    score           INTEGER NOT NULL DEFAULT 0,
                    max_score       INTEGER NOT NULL DEFAULT 0,
                    accuracy        REAL,
                    submitted_at    TIMESTAMP,
                    question_detail JSON
                )
            """ if is_sqlite else """
                CREATE TABLE candidate_results (
                    id              SERIAL PRIMARY KEY,
                    session_id      INTEGER NOT NULL REFERENCES exam_sessions(id),
                    phase           INTEGER NOT NULL,
                    domain          VARCHAR,
                    total_questions INTEGER NOT NULL DEFAULT 0,
                    attempted       INTEGER NOT NULL DEFAULT 0,
                    correct         INTEGER NOT NULL DEFAULT 0,
                    incorrect       INTEGER NOT NULL DEFAULT 0,
                    unanswered      INTEGER NOT NULL DEFAULT 0,
                    score           INTEGER NOT NULL DEFAULT 0,
                    max_score       INTEGER NOT NULL DEFAULT 0,
                    accuracy        DOUBLE PRECISION,
                    submitted_at    TIMESTAMP,
                    question_detail JSONB
                )
            """))
            log.info("  Created candidate_results table")
        else:
            log.info("  Skipped (exists): candidate_results table")

        # ------------------------------------------------------------------ #
        # 4. Add indexes (IF NOT EXISTS is supported in SQLite 3.3+ and PG)
        # ------------------------------------------------------------------ #
        indexes = [
            ("ix_exam_sessions_user_id",   "exam_sessions",      "user_id"),
            ("ix_exam_sessions_status",    "exam_sessions",      "status"),
            ("ix_answers_session_id",      "answers",            "session_id"),
            ("ix_answers_question_id",     "answers",            "question_id"),
            ("ix_violations_session_id",   "violations",         "session_id"),
            ("ix_violations_created_at",   "violations",         "created_at"),
            ("ix_questions_domain",        "questions",          "domain"),
            ("ix_questions_phase",         "questions",          "phase"),
            ("ix_candidate_results_sid",   "candidate_results",  "session_id"),
        ]
        for idx_name, tbl, col in indexes:
            try:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {tbl}({col})"
                ))
                log.info(f"  Index ensured: {idx_name}")
            except Exception as e:
                log.warning(f"  Index skipped ({idx_name}): {e}")

        # ------------------------------------------------------------------ #
        # 6. Domain renames — safe UPDATE, no data loss
        # ------------------------------------------------------------------ #
        # 6a. Reassign "Agricultural - Physiology" questions → "Bio-Science"
        conn.execute(text(
            "UPDATE questions SET domain = 'Bio-Science' "
            "WHERE domain = 'Agricultural - Physiology'"
        ))
        # 6b. Rename "Computer Science & Engineering" → "Computer Science"
        conn.execute(text(
            "UPDATE questions SET domain = 'Computer Science' "
            "WHERE domain = 'Computer Science & Engineering'"
        ))
        # 6c. Update candidate_results domain for historical records
        conn.execute(text(
            "UPDATE candidate_results SET domain = 'Bio-Science' "
            "WHERE domain = 'Agricultural - Physiology'"
        ))
        conn.execute(text(
            "UPDATE candidate_results SET domain = 'Computer Science' "
            "WHERE domain = 'Computer Science & Engineering'"
        ))
        # 6d. Update any in-progress session paper2_domain values
        conn.execute(text(
            "UPDATE exam_sessions SET paper2_domain = 'Bio-Science' "
            "WHERE paper2_domain = 'Agricultural - Physiology'"
        ))
        conn.execute(text(
            "UPDATE exam_sessions SET paper2_domain = 'Computer Science' "
            "WHERE paper2_domain = 'Computer Science & Engineering'"
        ))
        log.info("  Domain renames applied: Physiology→Bio-Science, CS&E→Computer Science")

        conn.commit()

    # ------------------------------------------------------------------ #
    # 5. Use SQLAlchemy create_all to pick up any remaining new tables
    # ------------------------------------------------------------------ #
    Base.metadata.create_all(bind=engine)
    log.info("SQLAlchemy create_all completed (safe no-op if tables exist).")
    log.info("Migration complete. ✓")


if __name__ == "__main__":
    run_migration()
