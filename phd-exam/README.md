# PhD Entrance Examination Platform

Server-authoritative, AI-proctored exam platform for 50–100 concurrent
candidates. FastAPI + SQLAlchemy backend, vanilla HTML/CSS/JS frontend
(Jinja2-rendered, no build step), WebSocket-driven admin console.

## Reaching 500+ questions across your 15 domains

Log in as `setter` or `admin` → **Question bank**. Rename the 15 placeholder
domains in `backend/config.py`'s `DOMAINS` list to your real subject areas
first — everything else reads from that list.

- One-off edits: "+ Add question" (domain dropdown, MCQ or written).
- Reaching scale: "Download CSV template" → fill it in Excel/Sheets → export
  CSV → "Bulk import (CSV)". Columns: `domain, question_type, question_text,
  option_a..option_d, correct_option`. Leave option columns blank for
  written questions. `correct_option` accepts `A/B/C/D` or `1-4`.
  Bad rows are skipped and reported by row number — valid rows still import.

Each candidate gets `EXAM_QUESTION_COUNT` questions (config.py, default 20):
exactly half MCQ (Part 1) + half written (Part 2), drawn randomly from the
whole bank, same split for everyone. `/exam/start` returns a clear 409 —
not a crash — if the bank doesn't yet have enough of either type.

## Getting Started & Running the Platform

### Prerequisites
- Python 3.8+ installed on your system.

### Installation & Running (Windows)
Open Command Prompt or PowerShell and run:
```powershell
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Installation & Running (Mac/Linux)
Open Terminal and run:
```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

## How to Use the Application

Once the server is running, open your browser and go to `http://localhost:8000`. 
On the first run, the server will automatically create a local SQLite database (`exam_platform.db`) and seed it with demo accounts.

### Demo Accounts
You can use the following credentials to log in at `http://localhost:8000/login`:
- **Admin**: The email and password configured in the environment (`ADMIN_EMAIL`, `ADMIN_PASSWORD`)
- *(Note: The old auto-bypassing tester accounts were removed for production security. If you need to inject test accounts on demand without wiping the database, you can now run the standalone script: `python -m backend.seed_db`)*
### Workflows

**1. Candidate Workflow (Taking an Exam)**
- Log in using the Candidate credentials.
- First-time login will prompt for a mandatory password reset.
- Proceed to `/exam` to start the exam.
- *Note: Ensure your webcam is enabled and you remain in full-screen mode to avoid getting flagged by the AI proctor.*

**2. Administrator Workflow (Monitoring)**
- Log in using the Admin credentials.
- You will land on `/admin`, the Admin Dashboard.
- From here, you can monitor active exam sessions in real-time, including time remaining and any AI proctor violations (e.g., face not detected, tab switching).

**3. Question Setter Workflow (Managing Questions)**
- Log in using the Setter or Admin credentials.
- Navigate to the **Question Bank** section.
- Add questions manually by clicking "+ Add question" or use the bulk import feature using a CSV file.

### Resetting the Database
To wipe all state (including exam attempts and added questions) and reseed the demo accounts: 
1. Stop the running server (Ctrl+C).
2. Delete the `exam_platform.db` file in the project folder.
3. Restart the server.

### Re-running the verification script

`smoke_test.sh` exercises the full server-authoritative flow end to end —
login, the reset-password gate, starting an exam, answering, firing three
violations, confirming the token is dead everywhere (not just on exam
routes), confirming a second attempt is blocked, and an admin snapshot —
against a running instance on port 8811:

```bash
uvicorn backend.main:app --port 8811 &
./smoke_test.sh
```

## What's actually implemented

- **Server-authoritative everything.** Question order and MCQ option order
  are shuffled once per session with a server-side RNG seed and stored in
  the DB; the client only ever sees display indices. The countdown is
  derived from a stored `ends_at` timestamp, recomputed on every request —
  a client can't extend or freeze it. Every exam-mutating endpoint re-checks
  session status server-side before doing anything.
- **3-strike rule with real consequences.** Violations increment a DB
  counter transactionally; the 3rd strike flips the session to
  `DISQUALIFIED` and bumps a `token_version` counter on the user row in the
  same transaction. Every subsequent request with that candidate's old JWT
  — not just exam routes, any authenticated route — is rejected with 401,
  because `get_current_user` checks the token's embedded version against
  the user's current one on every call. A disqualified or completed
  candidate also can't start a second attempt (`already_used` check).
- **Full-screen / tab-switch / copy / right-click interception** via
  `fullscreenchange`, `visibilitychange`, `copy`, and `contextmenu`
  listeners in `exam.js`, each reporting to `/exam/violation`.
- **Webcam face tracking** via face-api.js (TinyFaceDetector, loaded from
  a CDN) running client-side, flagging `face_not_detected`,
  `multiple_faces`, and `looking_away` (bounding-box-offset heuristic) —
  all routed through the same server-owned strike pipeline.
- **One-question-at-a-time UI** with Previous/Next, mark-for-review, a
  live server-synced countdown, and a color-coded nav grid.
- **Admin console**: REST snapshot on load, live updates over
  `/ws/admin`, plus a 5s polling fallback so the countdown columns keep
  ticking even between WebSocket events.

Verified by hand (see `smoke_test.sh` output): the full happy path, the
reset-password gate blocking exam access, shuffled options differing from
canonical order, 3-strike disqualification, JWT revocation on
disqualification, blocked re-attempts, and server-side auto-expiry when
`ends_at` passes.

## Known limitations — read before calling this "production-ready"

I'm not going to oversell this. It's a solid, correctly-architected MVP.
Before a real cohort sits an exam on it, you'd still need to deal with:

- **Single-process WebSocket broadcasting.** `websocket_manager.py` keeps
  connections in an in-memory set. Fine for one `uvicorn` worker handling
  50–100 candidates. Scale to multiple worker processes or machines and
  admin broadcasts silently stop reaching sockets connected to a different
  process — you'd need Redis Pub/Sub or similar. Flagged in the code
  comment, not hidden. This is exactly why `deploy/gunicorn.conf.py` runs
  **1** worker instead of the "2 x vCPU + 1" a 4-core box would normally
  get — see the comment block at the top of that file for the full
  reasoning and the upgrade path.
- **SQLite** is fine for this scale on one box but has no real concurrent
  write story. Point `EXAM_DATABASE_URL` at Postgres for anything beyond a
  pilot.
- **Browser-side proctoring has a hard ceiling.** However well the JS is
  written, the browser is still an untrusted client. A determined
  candidate can still find ways around client-side detection (a second
  device out of frame, a virtual camera feed, etc.) that no amount of
  polish in this codebase closes — that's a property of browser-based
  proctoring generally, not a gap specific to this implementation. Treat
  the strike log as evidence for human review, not as an infallible
  verdict.
- **"Looking away" detection is a bounding-box-offset heuristic**, not
  real head-pose/gaze estimation. It'll catch someone turning well off to
  the side; it won't catch subtle eye movement.
- **A page reload mid-exam doesn't add a strike.** The exam resumes at the
  candidate's saved position (server-authoritative state makes that safe),
  but the few seconds where the camera and fullscreen listeners are down
  during the reload aren't themselves flagged. Worth deciding deliberately
  whether a reload should count as an infraction for your use case.
- **No recording/audit trail beyond the violation log** — no video
  snapshots stored, no human-reviewable clip tied to each flagged event.
  For any exam with real stakes, an admissions committee will likely want
  more than "3 violations, type X, timestamp Y" to act on a disqualification.
- **JWT secret and CORS** are left at development defaults
  (`config.py`). Set `EXAM_SECRET_KEY` via environment variable and lock
  down allowed origins before deploying anywhere reachable from the
  internet.
- **Legal/consent**: continuous webcam monitoring of candidates has real
  privacy and disclosure obligations depending on jurisdiction and
  institution policy — this is a technical implementation, not legal
  sign-off that your proctoring approach is compliant.

## Project layout

```
backend/
  main.py              FastAPI app, page routes, static/template mounting
  config.py             All tunable constants (exam duration, strikes, etc.)
  database.py            SQLAlchemy engine/session
  models.py               User, Question, ExamSession, Answer, Violation
  schemas.py                Pydantic request/response models
  security.py                 Password hashing, JWT issuance + revocation
  exam_engine.py                Shuffling, timing, strikes — the exam's core logic
  websocket_manager.py            Admin live-broadcast connection manager
  seed.py                           Demo accounts + 5-question mock bank
  routers/
    auth.py       /auth/login, /auth/reset-password, /auth/me
    exam.py       All candidate-facing exam endpoints
    admin.py      /admin/sessions REST + /ws/admin live feed
frontend/
  templates/       login.html, reset_password.html, exam.html, admin_dashboard.html
  static/css/      styles.css — the whole design system
  static/js/       login.js, reset_password.js, exam.js, proctor.js, admin.js
requirements.txt
smoke_test.sh      End-to-end curl/websocket verification script
```
