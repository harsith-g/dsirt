"""Gunicorn configuration.

4 Uvicorn workers, as specified. Note that the signalling gateway is
Redis-backed precisely because these workers do not share memory -- see
``app/services/signaling.py``.
"""
from __future__ import annotations

import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "4"))
worker_class = "uvicorn.workers.UvicornWorker"

# WebSockets are long-lived: a request timeout would sever proctoring feeds.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 65

# Recycling workers bounds any slow memory growth over a long exam window.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = 200

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%({x-forwarded-for}i)s %(m)s %(U)s %(s)s %(L)ss'

forwarded_allow_ips = "*"  # we sit behind our own Nginx
proc_name = "examportal"


def on_starting(server):  # pragma: no cover
    server.log.info(
        "starting %s workers (%s); CPUs available: %s",
        workers,
        worker_class,
        multiprocessing.cpu_count(),
    )
