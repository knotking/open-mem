"""Two stores, because they are two different things.

`audit_events` holds everything that is not a read: writes, ACL changes, shares,
config, key issuance, break-glass. Low volume, never purged.

`access_log` holds reads. It is written on every read, never updated, queried by
time range -- and it must **survive the deletion of what it describes**, which is
why it carries no foreign keys. You cannot evidence "we deleted it" if the
evidence lived inside the deletion.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from .auth import Principal
from .ids import new_id


async def record_audit(
    conn: asyncpg.Connection | asyncpg.Pool,
    principal: Principal,
    *,
    action: str,
    project_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_events (event_id, org_id, project_id, actor_user_id,
                                  actor_key_id, actor_mode, action, target_type,
                                  target_id, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        new_id("aud"),
        principal.org_id,
        project_id,
        principal.user_id,
        principal.key_id,
        principal.mode,
        action,
        target_type,
        target_id,
        detail or {},
    )


async def record_access(
    conn: asyncpg.Connection | asyncpg.Pool,
    principal: Principal,
    *,
    action: str,
    project_id: str | None = None,
    data_id: str | None = None,
    query_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO access_log (access_id, org_id, project_id, user_id, key_id,
                                principal, action, data_id, query_id, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        new_id("acc"),
        principal.org_id,
        project_id,
        principal.user_id,
        principal.key_id,
        f"user:{principal.user_id}",
        action,
        data_id,
        query_id,
        detail or {},
    )


async def record_access_many(
    conn: asyncpg.Connection | asyncpg.Pool,
    principal: Principal,
    *,
    action: str,
    project_id: str,
    data_ids: list[str],
    query_id: str,
) -> None:
    if not data_ids:
        return
    await conn.executemany(
        """
        INSERT INTO access_log (access_id, org_id, project_id, user_id, key_id,
                                principal, action, data_id, query_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        [
            (
                new_id("acc"),
                principal.org_id,
                project_id,
                principal.user_id,
                principal.key_id,
                f"user:{principal.user_id}",
                action,
                data_id,
                query_id,
            )
            for data_id in data_ids
        ],
    )
