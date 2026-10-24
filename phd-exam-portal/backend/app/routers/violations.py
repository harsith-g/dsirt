"""Violation intake -- `/api/violations`.

Snapshot writes are pushed to a thread so the event loop never blocks on disk
inside a request handler.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import api_rate_limiter, client_ip, get_current_candidate
from app.db.session import get_db
from app.enums import ActorType
from app.models import Candidate, ExamSession, Violation
from app.schemas import MessageOut, ViolationCreate, ViolationOut
from app.services.audit import log_action
from app.services.signaling import publish_event

router = APIRouter(prefix="/api", tags=["proctoring"], dependencies=[Depends(api_rate_limiter)])

_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", re.DOTALL)
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def _write_snapshot_sync(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


async def _store_snapshot(session_id: uuid.UUID, data_url: str) -> str | None:
    m = _DATA_URL.match(data_url.strip())
    if not m:
        return None
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    try:
        blob = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not blob or len(blob) > _MAX_SNAPSHOT_BYTES:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    name = f"{session_id}/{stamp}.{ext}"
    path = Path(settings.SNAPSHOT_DIR) / name
    # Blocking file I/O belongs in a worker thread, never in the loop.
    await asyncio.to_thread(_write_snapshot_sync, path, blob)
    return f"{settings.SNAPSHOT_URL_PREFIX}/{name}"


@router.post("/violations", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def report_violation(
    payload: ViolationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    session = (
        await db.execute(
            select(ExamSession)
            .where(ExamSession.candidate_id == candidate.id)
            .order_by(ExamSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No examination session found."
        )

    snapshot_url = None
    if payload.snapshot_data_url:
        snapshot_url = await _store_snapshot(session.id, payload.snapshot_data_url)

    violation = Violation(
        session_id=session.id,
        candidate_id=candidate.id,
        violation_type=payload.violation_type,
        details=payload.details,
        snapshot_url=snapshot_url,
    )
    db.add(violation)
    session.violation_count = (session.violation_count or 0) + 1

    auto_flagged = False
    if session.violation_count >= settings.MAX_VIOLATIONS_BEFORE_FLAG:
        # Flag for review; the decision to disqualify stays with a human.
        auto_flagged = True

    await log_action(
        db,
        actor_type=ActorType.CANDIDATE,
        action=f"violation.{payload.violation_type.value.lower()}",
        actor_id=candidate.id,
        actor_label=candidate.registration_number,
        target_type="exam_session",
        target_id=str(session.id),
        payload={"count": session.violation_count, "details": payload.details},
        ip_address=client_ip(request),
    )
    await db.commit()

    # Push to any proctor watching this candidate, on any worker.
    await publish_event(
        "proctor:events",
        {
            "type": "violation",
            "session_id": str(session.id),
            "candidate_id": str(candidate.id),
            "registration_number": candidate.registration_number,
            "violation_type": payload.violation_type.value,
            "count": session.violation_count,
            "flagged": auto_flagged,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return MessageOut(
        message="Violation recorded."
        + (" This session has been flagged for proctor review." if auto_flagged else "")
    )


@router.get("/violations/mine", response_model=list[ViolationOut])
async def my_violations(
    db: AsyncSession = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
):
    rows = (
        (
            await db.execute(
                select(Violation)
                .where(Violation.candidate_id == candidate.id)
                .order_by(Violation.timestamp.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
