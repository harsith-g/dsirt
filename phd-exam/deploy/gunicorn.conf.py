"""
Gunicorn configuration for the PhD Entrance Examination platform, tuned for
the target Hostinger KVM 4 VPS (4 vCPU / 16 GB RAM, 62.72.29.111 /
srv1990910.hstgr.cloud).

Run with:
    gunicorn backend.main:app -c deploy/gunicorn.conf.py

------------------------------------------------------------------------
IMPORTANT — workers is intentionally 1, not the "2 x vCPU + 1 = 9" you'd
normally reach for on a 4-core box. Read this before "fixing" it.
------------------------------------------------------------------------
backend/websocket_manager.py keeps candidate and admin WebSocket
connections in an in-memory, per-process ConnectionManager (see its own
module docstring). Gunicorn workers are separate OS processes with
separate Python interpreters and separate copies of that manager. A
WebSocket connection is pinned to whichever single worker process
accepted it for its whole lifetime.

With workers > 1:
  - A candidate's live camera frames (`proctor_frame`) are relayed to
    admin dashboards ONLY within the same worker process. There is no
    REST fallback for this — an admin whose /ws/admin connection landed
    on a different worker than a given candidate's /exam/ws/candidate
    connection would see that one candidate's tile stuck on "No Video"
    forever, even though the candidate's camera is working fine. With 9
    workers and 100+ candidates spread pseudo-randomly across them, most
    admin sessions would see most candidates' cameras as offline. This
    directly breaks the "high-quality live camera proctoring" grid this
    project is built around — silently, with no error anywhere.
  - Admin actions that push over WebSocket (global "Start/End Exam for
    All", an individual disqualify's forced-logout push) only reach
    candidate sockets held by the SAME worker process that handled the
    admin's HTTP request. Everything critical (session status, phase
    lock, disqualification) is still enforced server-side on every
    request regardless of which worker serves it, and the candidate UI
    re-syncs via a 15s REST poll (frontend/static/js/exam.js) as a
    backstop — so a multi-worker deployment degrades "instant" to
    "up to ~15s delayed" here rather than actually breaking, but that's
    still a real regression from the single-worker behavior.

A single `uvicorn.workers.UvicornWorker` running asyncio can comfortably
handle 100-200 concurrent candidates for this workload — mostly small
JSON API calls plus a steady trickle of already-compressed JPEG frames
at 1-2 FPS per candidate (see frontend/static/js/proctor.js) — since none
of it is CPU-bound. The other 3 vCPUs on this box are doing useful work
elsewhere: PostgreSQL, nginx, and OS/network overhead.

To go beyond one worker (or beyond one machine) correctly, migrate
websocket_manager.py's broadcast/relay logic to a shared pub/sub layer
(Redis Pub/Sub is the natural fit) so every worker process can see every
connection's events. That's a real, scoped follow-up — not a one-line
config change — and is out of scope for this PR.
------------------------------------------------------------------------
"""
import multiprocessing  # noqa: F401  (kept for reference; see workers note above)

# --- Networking ------------------------------------------------------------
# nginx (deploy/nginx.conf) reverse-proxies to this loopback address only —
# gunicorn is never exposed directly to the internet.
bind = "127.0.0.1:8000"

# --- Workers -----------------------------------------------------------
# See the module docstring above for why this is 1, not 9, on this codebase.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"

# CRITICAL: must stay False. backend/database.py creates the global
# SQLAlchemy `engine` (and its connection pool) at import time. With
# preload_app=True, gunicorn would import the app ONCE in the master
# process before forking — every worker would then inherit and share the
# SAME already-connected pool/sockets across the fork, which corrupts
# connection state and causes intermittent "connection already closed" /
# deadlock-looking errors under load. Left at False (the default),
# each worker imports the app fresh after forking and gets its own
# independent engine/pool. Doesn't matter much at workers=1, but keep
# this False if you ever do move to more workers behind Redis pub/sub.
preload_app = False

# --- Timeouts ------------------------------------------------------------
# Long enough that a slow question-bank ZIP import or a burst of
# concurrent answer-saves under load doesn't get killed mid-request.
# WebSocket connections are NOT subject to this worker timeout the same
# way plain HTTP requests are (uvicorn's own WebSocket loop manages
# those; see CANDIDATE_WS_IDLE_TIMEOUT_SECONDS / ADMIN_WS_IDLE_TIMEOUT_SECONDS
# in backend/routers/exam.py and admin.py), but nginx's own
# proxy_read_timeout (deploy/nginx.conf) is what actually needs to be
# large for those.
timeout = 120

# Keep-alive for HTTP/1.1 connections nginx holds open to gunicorn.
keepalive = 5

# --- Logging -------------------------------------------------------------
# Plain stdout/stderr — systemd (deploy/phd-exam.service) captures these
# into the journal (`journalctl -u phd-exam -f`).
accesslog = "-"
errorlog = "-"
loglevel = "info"

# --- Process naming --------------------------------------------------------
proc_name = "phd-exam"

# --- Restarts --------------------------------------------------------------
# Recycle a worker after this many requests to bound the effect of any
# slow memory growth over a long exam window; jitter avoids every worker
# (irrelevant at workers=1 today, harmless if that ever changes) recycling
# at the exact same moment.
max_requests = 2000
max_requests_jitter = 200

# Give an in-flight request/WebSocket loop time to finish cleanly on
# graceful reload/shutdown rather than being hard-killed mid-response.
graceful_timeout = 30
