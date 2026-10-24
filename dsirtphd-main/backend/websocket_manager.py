"""
Broadcasts candidate state changes to connected admin dashboards,
and delivers global commands (exam open/close) to all candidate sockets.

This is a single-process, in-memory manager: fine for one uvicorn worker
serving 50-100 candidates. If you scale to multiple worker processes, this
needs to move to a shared pub/sub layer (Redis Pub/Sub) since in-memory
sets don't cross process boundaries.

Camera architecture:
  - Live monitoring: base64 WebP frames relayed to admin at LOW frequency
    (1 frame per PROCTOR_RELAY_INTERVAL_SECONDS, default 10s).
  - Audit snapshots: 1 image per PROCTOR_SNAPSHOT_INTERVAL_SECONDS saved to disk.
  - This dramatically reduces CPU, disk I/O and network vs. the original
    continuous streaming approach.
"""
import asyncio
import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Set

import aiofiles
from fastapi import WebSocket

from backend.config import UPLOADS_DIR

# How often to relay a live frame to admin dashboards (seconds).
# At 1 per 10s for 50 candidates: 5 frames/sec to admin — very manageable.
PROCTOR_RELAY_INTERVAL_SECONDS = int(os.environ.get("PROCTOR_RELAY_INTERVAL_SECONDS", "10"))

# How often to save an audit snapshot to disk (seconds).
PROCTOR_SNAPSHOT_INTERVAL_SECONDS = int(os.environ.get("PROCTOR_SNAPSHOT_INTERVAL_SECONDS", "10"))


class ConnectionManager:
    def __init__(self):
        self._admin_sockets: Set[WebSocket] = set()
        self._candidate_sockets: dict[int, WebSocket] = {}   # user_id -> socket
        self._lock = asyncio.Lock()

        # Throttle relay: track last relay time per user_id
        self._last_relay_at: dict[int, float] = {}
        # Throttle disk snapshot: track last save time per user_id
        self._last_snapshot_at: dict[int, float] = {}

    # ------------------------------------------------------------------ #
    # Admin connections
    # ------------------------------------------------------------------ #

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._admin_sockets.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._admin_sockets.discard(websocket)

    async def broadcast(self, payload: dict):
        """Send a JSON payload to all connected admin dashboards."""
        dead = []
        for ws in list(self._admin_sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._admin_sockets.discard(ws)

    async def send_to_admins(self, payload: dict):
        await self.broadcast(payload)

    # ------------------------------------------------------------------ #
    # Candidate connections
    # ------------------------------------------------------------------ #

    async def connect_candidate(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._candidate_sockets[user_id] = websocket

    async def disconnect_candidate(self, user_id: int):
        async with self._lock:
            self._candidate_sockets.pop(user_id, None)
            self._last_relay_at.pop(user_id, None)
            self._last_snapshot_at.pop(user_id, None)

    async def send_to_candidate(self, user_id: int, payload: dict):
        async with self._lock:
            ws = self._candidate_sockets.get(user_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect_candidate(user_id)

    async def send_global_command(self, payload: dict):
        """
        Broadcast a command to ALL connected candidate sockets simultaneously.
        Used for global exam open/close events.
        """
        dead = []
        async with self._lock:
            sockets = list(self._candidate_sockets.items())
        for user_id, ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(user_id)
        if dead:
            async with self._lock:
                for uid in dead:
                    self._candidate_sockets.pop(uid, None)

    # ------------------------------------------------------------------ #
    # Proctor frame handling — throttled relay + periodic snapshot
    # ------------------------------------------------------------------ #

    async def handle_proctor_frame(self, user_id: int, session_id: int, frame_data: str):
        """
        Handle an incoming WebP frame (base64-encoded) from a candidate.

        Throttled relay:
          Only relay to admin dashboards if PROCTOR_RELAY_INTERVAL_SECONDS
          have elapsed since the last relay for this candidate. This prevents
          50 candidates overwhelming the admin WebSocket with continuous frames.

        Periodic audit snapshot:
          Save to disk only once per PROCTOR_SNAPSHOT_INTERVAL_SECONDS.
          Disk writes are non-blocking (asyncio.create_task).
        """
        now = asyncio.get_event_loop().time()

        # Throttled live relay to admin
        last_relay = self._last_relay_at.get(user_id, 0)
        if now - last_relay >= PROCTOR_RELAY_INTERVAL_SECONDS:
            self._last_relay_at[user_id] = now
            await self.broadcast({
                "type": "proctor_frame",
                "user_id": user_id,
                "session_id": session_id,
                "frame": frame_data,
            })

        # Throttled disk snapshot for audit
        last_snap = self._last_snapshot_at.get(user_id, 0)
        if now - last_snap >= PROCTOR_SNAPSHOT_INTERVAL_SECONDS:
            self._last_snapshot_at[user_id] = now
            asyncio.create_task(self._save_frame_to_disk(user_id, frame_data))

    async def _save_frame_to_disk(self, user_id: int, frame_data: str):
        """
        Write an incoming base64-encoded WebP frame to:
          UPLOADS_DIR/{user_id}/{timestamp_ms}.webp
        Errors are logged but do not propagate.
        """
        try:
            user_dir = UPLOADS_DIR / str(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)

            if "," in frame_data:
                frame_data = frame_data.split(",", 1)[1]

            img_bytes = base64.b64decode(frame_data)
            timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
            filepath = user_dir / f"{timestamp_ms}.webp"
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(img_bytes)
        except Exception:
            pass  # Best-effort; never crash candidate session on disk error


manager = ConnectionManager()
