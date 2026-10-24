"""Append-only audit trail."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActorType, AuditLog


async def log_action(
    db: AsyncSession,
    *,
    actor_type: ActorType,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:1024] or None,
    )
    db.add(entry)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return entry
