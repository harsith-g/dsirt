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

# ---------------------------------------------------------------------------
# Domain <-> filename inference — the single shared table used both by the
# legacy docx-lookup in routers/exam.py and by the question-bank ZIP
# importer (services/ingestion.py) to figure out which domain a file
# inside an upload belongs to, from its name alone (e.g. "agronomy.csv",
# "AGRONOMY-Final.docx"). Keep this in one place so the two callers never
# drift into disagreeing about what a given filename means.
# ---------------------------------------------------------------------------
DOMAIN_FILENAME_KEYWORDS: dict[str, list[str]] = {
    "Agricultural - Agronomy":       ["agronomy"],
    "Agricultural - Entomology":     ["entomology"],
    "Agricultural - Pathology":      ["pathology"],
    "Bio-Science":                   ["bio", "lif", "life"],
    "Computer Science":              ["computer", "cs ", "cse"],
    "Commerce & Management Studies": ["commerce", "management"],
    "English":                       ["eng qp", "english", "eng "],
    "Mathematics":                   ["maths", "math"],
    "Tamil":                         ["tamil"],
    "Research Methodology":          ["research_methodology", "research methodology", "researchmethodology"],
}

# Canonical lookup: lower-case alias -> canonical domain name. Includes
# retired names so any in-flight/historical values still normalise.
DOMAIN_ALIASES: dict[str, str] = {k.lower(): k for k in DOMAIN_FILENAME_KEYWORDS}
DOMAIN_ALIASES["computer science & engineering"] = "Computer Science"
DOMAIN_ALIASES["agricultural - physiology"] = "Bio-Science"


def normalise_domain(domain: str) -> str:
    return DOMAIN_ALIASES.get((domain or "").strip().lower(), (domain or "").strip())


def infer_domain_from_filename(filename: str) -> "str | None":
    """Best-effort match of an uploaded file's name to a configured domain,
    e.g. 'agronomy.csv' -> 'Agricultural - Agronomy'. Returns None if
    nothing matches, so the caller can fall back to a form-supplied
    default or a per-row 'domain' column instead."""
    name = (filename or "").lower()
    for domain, keywords in DOMAIN_FILENAME_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return domain
    return None


def infer_phase_for_domain(domain: "str | None") -> int:
    """Research Methodology is always Phase 1; every other configured
    domain is Phase 2. Used as a fallback when neither the upload form nor
    a 'phase' column tells us explicitly."""
    if domain and normalise_domain(domain) == "Research Methodology":
        return 1
    return 2

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
