"""WebSocket signalling endpoints.

`/ws/signaling/{candidate_id}` -- the candidate side, and
`/ws/proctor`                  -- the admin side.

WebSockets cannot use the `Depends`-based HTTP guards, so each endpoint
authenticates the cookie itself *before* accepting the handshake and closes
with 4401/4403 rather than accepting and then disconnecting.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import AsyncSessionLocal
from app.models import Admin, Candidate, ExamSession
from app.services.signaling import (
    broadcast_to_admins,
    publish_event,
    registry,
    relay,
)

logger = logging.getLogger("proctoring.ws")
router = APIRouter(tags=["proctoring:ws"])

WS_UNAUTHORISED = 4401
WS_FORBIDDEN = 4403

# Message types a candidate may originate, and those an admin may originate.
CANDIDATE_SIGNALS = {"offer", "answer", "ice-candidate", "frame", "stream-state"}
ADMIN_SIGNALS = {"offer", "answer", "ice-candidate", "request-stream", "stop-stream"}


async def _auth_candidate(ws: WebSocket) -> Candidate | None:
    token = ws.cookies.get(settings.CANDIDATE_COOKIE_NAME)
    if not token:
        return None
    claims = decode_token(token, "candidate")
    if not claims:
        return None
    async with AsyncSessionLocal() as db:
        try:
            return await db.get(Candidate, uuid.UUID(claims["sub"]))
        except (ValueError, KeyError):
            return None


async def _auth_admin(ws: WebSocket) -> Admin | None:
    token = ws.cookies.get(settings.ADMIN_COOKIE_NAME)
    if not token:
        return None
    claims = decode_token(token, "admin")
    if not claims:
        return None
    async with AsyncSessionLocal() as db:
        try:
            admin = await db.get(Admin, uuid.UUID(claims["sub"]))
        except (ValueError, KeyError):
            return None
        return admin if admin and admin.is_active else None


@router.websocket("/ws/signaling/{candidate_id}")
async def candidate_signalling(websocket: WebSocket, candidate_id: str):
    candidate = await _auth_candidate(websocket)
    if candidate is None:
        await websocket.close(code=WS_UNAUTHORISED)
        return
    # A candidate may only ever open the socket for their own id.
    if str(candidate.id) != candidate_id:
        await websocket.close(code=WS_FORBIDDEN)
        return

    await websocket.accept()
    await registry.add_candidate(candidate_id, websocket)

    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(
                select(ExamSession)
                .where(ExamSession.candidate_id == candidate.id)
                .order_by(ExamSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    session_id = str(session.id) if session else None

    await publish_event(
        "proctor:events",
        {
            "type": "candidate-online",
            "candidate_id": candidate_id,
            "session_id": session_id,
            "registration_number": candidate.registration_number,
            "full_name": candidate.full_name,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await websocket.send_text(
        json.dumps({"type": "welcome", "ice_servers": settings.ice_servers})
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind not in CANDIDATE_SIGNALS:
                continue
            target_admin = msg.get("to")
            payload = {
                "type": kind,
                "from": candidate_id,
                "session_id": session_id,
                "registration_number": candidate.registration_number,
                "payload": msg.get("payload"),
            }
            if target_admin:
                await relay(to_role="admin", to_id=str(target_admin), message=payload)
            else:
                await broadcast_to_admins(payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("candidate socket error (%s): %s", candidate_id, exc)
    finally:
        await registry.drop_candidate(candidate_id)
        await publish_event(
            "proctor:events",
            {
                "type": "candidate-offline",
                "candidate_id": candidate_id,
                "session_id": session_id,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )


@router.websocket("/ws/proctor")
async def proctor_signalling(websocket: WebSocket):
    admin = await _auth_admin(websocket)
    if admin is None:
        await websocket.close(code=WS_UNAUTHORISED)
        return

    admin_id = str(admin.id)
    await websocket.accept()
    await registry.add_admin(admin_id, websocket)
    await websocket.send_text(
        json.dumps(
            {"type": "welcome", "admin_id": admin_id, "ice_servers": settings.ice_servers}
        )
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "watch":
                await registry.watch(admin_id, str(msg.get("candidate_id")))
                continue
            if kind == "unwatch":
                await registry.unwatch(admin_id, str(msg.get("candidate_id")))
                continue
            if kind not in ADMIN_SIGNALS:
                continue
            target = msg.get("to")
            if not target:
                continue
            await relay(
                to_role="candidate",
                to_id=str(target),
                message={
                    "type": kind,
                    "from": admin_id,
                    "payload": msg.get("payload"),
                },
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("proctor socket error (%s): %s", admin_id, exc)
    finally:
        await registry.drop_admin(admin_id)
