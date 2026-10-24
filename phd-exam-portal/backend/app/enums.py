"""Domain enums.

Kept free of SQLAlchemy so schemas, the importer and the offline parsing
tools can import them without pulling in the ORM or a database driver.
"""
from __future__ import annotations

import enum


class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    PROCTOR = "PROCTOR"


class CandidateStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    DISQUALIFIED = "DISQUALIFIED"


class Phase(str, enum.Enum):
    PHASE_1 = "PHASE_1"
    PHASE_2 = "PHASE_2"


class QuestionType(str, enum.Enum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    TRUE_FALSE = "TRUE_FALSE"
    TEXT = "TEXT"


class Difficulty(str, enum.Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class SessionStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    PHASE_1_IN_PROGRESS = "PHASE_1_IN_PROGRESS"
    PHASE_1_COMPLETED = "PHASE_1_COMPLETED"
    PHASE_2_IN_PROGRESS = "PHASE_2_IN_PROGRESS"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"


class ViolationType(str, enum.Enum):
    TAB_SWITCH = "TAB_SWITCH"
    FULLSCREEN_EXIT = "FULLSCREEN_EXIT"
    CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    NO_FACE = "NO_FACE"
    WINDOW_BLUR = "WINDOW_BLUR"
    COPY_PASTE = "COPY_PASTE"
    DEVTOOLS = "DEVTOOLS"
    NETWORK_LOST = "NETWORK_LOST"


class ActorType(str, enum.Enum):
    ADMIN = "ADMIN"
    CANDIDATE = "CANDIDATE"
    SYSTEM = "SYSTEM"
