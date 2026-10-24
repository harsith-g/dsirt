"""
Central configuration for the exam platform.

Everything here is read once at import time. In a real deployment, pull
SECRET_KEY and DATABASE_URL from environment variables / a secrets manager —
they are hardcoded here only so the project runs immediately out of the box.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Security ---------------------------------------------------------
SECRET_KEY = os.environ.get("EXAM_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("EXAM_SECRET_KEY environment variable must be set in production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 240  # generous, exam sessions can run long
DEV_MODE = False

# --- Database -----------------------------------------------------------
DATABASE_URL = os.environ.get("EXAM_DATABASE_URL", "sqlite:///./exam_platform.db")

# --- Admin Details ------------------------------------------------------
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@examportal.edu")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD environment variable must be set in production")

# --- Exam rules (server-authoritative) -----------------------------------
EXAM_DURATION_MINUTES = 120
MAX_STRIKES = 3

EXAM_PAPER1_COUNT = 50
EXAM_PAPER2_COUNT = 50

# Universal default password for all candidate accounts
CANDIDATE_DEFAULT_PASSWORD = os.environ.get("CANDIDATE_DEFAULT_PASSWORD")
if not CANDIDATE_DEFAULT_PASSWORD:
    raise ValueError("CANDIDATE_DEFAULT_PASSWORD environment variable must be set")

# Directory where proctor frame snapshots are saved to disk
UPLOADS_DIR = Path(os.environ.get("EXAM_UPLOADS_DIR", "./uploads/proctor_streams"))

DOMAINS = [
    # Phase 1
    "Research Methodology",
    # Phase 2 domains
    "Agricultural - Agronomy",
    "Agricultural - Entomology",
    "Agricultural - Pathology",
    "Bio-Science",
    "Computer Science",
    "Commerce & Management Studies",
    "English",
    "Mathematics",
    "Tamil",
]

# How stale a candidate's "last seen" can be before the admin grid marks
# them as disconnected (seconds).
CANDIDATE_STALE_SECONDS = 20

# Violation types the client is allowed to report. Anything else is rejected
# so a tampered client can't invent violation types or spam arbitrary strings.
# AI-based violation types (face_not_detected, multiple_faces, looking_away)
# have been removed — human proctors monitor candidates via the admin video grid.
VALID_VIOLATION_TYPES = {
    "tab_switch",
    "fullscreen_exit",
    "copy_attempt",
    "right_click",
    "devtools_suspected",
    "camera_disconnected",
}
