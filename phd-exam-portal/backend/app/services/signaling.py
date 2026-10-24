"""WebRTC signalling relay.

The portal runs 4 Uvicorn workers behind Nginx, so the candidate's WebSocket
and the proctor's WebSocket routinely land on *different processes*. An
in-memory dict of sockets is therefore not enough: it would deliver offers
only when both parties happened to hit the same worker, which is roughly one
time in four.

Every worker keeps its own local socket registry and subscribes to one Redis
pub/sub channel. To send a message the sender publishes it; each worker checks
whether the addressee is local and, if so, writes to that socket. The Redis
round-trip is a fraction of a millisecond and the relay becomes worker-count
independent.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.core.redis_client import get_redis

logger = logging.getLogger("proctoring.signaling")

SIGNAL_CHANNEL = "signal:relay"
EVENT_CHANNEL = "proctor:events"


class ConnectionRegistry:
    """Sockets held by *this* worker, keyed by peer id."""

    def __init__(self) -> None:
        self._candidates: dict[str, WebSocket] = {}
        self._admins: dict[str, WebSocket] = {}
        # Which admins asked to watch which candidate.
        self._watchers: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def add_candidate(self, candidate_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._candidates[candidate_id] = ws

    async def add_admin(self, admin_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._admins[admin_id] = ws

    async def drop_candidate(self, candidate_id: str) -> None:
        async with self._lock:
            self._candidates.pop(candidate_id, None)
            self._watchers.pop(candidate_id, None)

    async def drop_admin(self, admin_id: str) -> None:
        async with self._lock:
            self._admins.pop(admin_id, None)
            for watchers in self._watchers.values():
                watchers.discard(admin_id)

    async def watch(self, admin_id: str, candidate_id: str) -> None:
        async with self._lock:
            self._watchers[candidate_id].add(admin_id)

    async def unwatch(self, admin_id: str, candidate_id: str) -> None:
        async with self._lock:
            self._watchers[candidate_id].discard(admin_id)

    def candidate(self, candidate_id: str) -> WebSocket | None:
        return self._candidates.get(candidate_id)

    def admin(self, admin_id: str) -> WebSocket | None:
        return self._admins.get(admin_id)

    def local_admin_ids(self) -> list[str]:
        return list(self._admins)


registry = ConnectionRegistry()


async def _safe_send(ws: WebSocket, message: dict[str, Any]) -> bool:
    try:
        await ws.send_text(json.dumps(message))
        return True
    except Exception:  # socket died between lookup and write
        return False


async def relay(
    *,
    to_role: str,
    to_id: str,
    message: dict[str, Any],
) -> None:
    """Send ``message`` to a peer that may live on any worker."""
    envelope = {"to_role": to_role, "to_id": to_id, "message": message}
    # Fast path: the addressee is on this worker.
    local = registry.candidate(to_id) if to_role == "candidate" else registry.admin(to_id)
    if local is not None and await _safe_send(local, message):
        return
    await get_redis().publish(SIGNAL_CHANNEL, json.dumps(envelope))


async def broadcast_to_admins(message: dict[str, Any]) -> None:
    await get_redis().publish(
        SIGNAL_CHANNEL, json.dumps({"to_role": "admin", "to_id": "*", "message": message})
    )


async def publish_event(channel: str, payload: dict[str, Any]) -> None:
    """Fan out a proctoring event (violation, join, leave) to all admins."""
    await get_redis().publish(channel, json.dumps(payload))


async def _dispatch(envelope: dict[str, Any]) -> None:
    to_role = envelope.get("to_role")
    to_id = envelope.get("to_id")
    message = envelope.get("message") or {}
    if to_role == "admin" and to_id == "*":
        for admin_id in registry.local_admin_ids():
            ws = registry.admin(admin_id)
            if ws is not None:
                await _safe_send(ws, message)
        return
    ws = registry.candidate(to_id) if to_role == "candidate" else registry.admin(to_id)
    if ws is not None:
        await _safe_send(ws, message)


async def pubsub_listener(stop: asyncio.Event) -> None:
    """Background task started once per worker at application startup."""
    redis = get_redis()
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(SIGNAL_CHANNEL, EVENT_CHANNEL)
    logger.info("signalling relay subscribed to %s, %s", SIGNAL_CHANNEL, EVENT_CHANNEL)
    try:
        while not stop.is_set():
            try:
                msg = await pubsub.get_message(timeout=1.0)
            except Exception as exc:  # pragma: no cover - transient redis blip
                logger.warning("pubsub read failed: %s", exc)
                await asyncio.sleep(1.0)
                continue
            if not msg:
                continue
            try:
                payload = json.loads(msg["data"])
            except (TypeError, ValueError):
                continue
            if msg["channel"] == SIGNAL_CHANNEL:
                await _dispatch(payload)
            else:
                # Proctor events go to every admin socket on this worker.
                for admin_id in registry.local_admin_ids():
                    ws = registry.admin(admin_id)
                    if ws is not None:
                        await _safe_send(ws, {"type": "event", **payload})
    finally:
        try:
            await pubsub.unsubscribe(SIGNAL_CHANNEL, EVENT_CHANNEL)
            await pubsub.aclose()
        except Exception:  # pragma: no cover
            pass
