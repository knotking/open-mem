"""Deleting an account, and the line it must not cross.

"Delete my data" sounds like one scope and is two. A person's *personal* data is
theirs: private items, uploads, anything that arrived through a connection they
authorised for themselves. Data that came through a **shared** connection is the
organisation's -- a team Slack, a shared drive -- and deleting it because the
person who connected it left would destroy a colleague's work as a side effect
of an HR event.

So this deletes what is personal and **retains what is shared, saying so**. The
dry run reports both counts separately, because "we deleted 400 records"
without "and kept 1,200 that belong to the project" is an answer that will be
disputed later.

Revocation is immediate and unconditional either way: keys, producers and
connections stop working before any data question is settled, because the
security half of offboarding must not wait on the retention half.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg

from .audit import record_audit
from .auth import ADMIN, CONFIG_WRITE, Principal
from .ids import new_id


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class AccountPlan:
    user_id: str
    delete_data_ids: list[str] = field(default_factory=list)
    retained: list[dict] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    keys: int = 0
    producers: int = 0
    connections: int = 0

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for row in self.retained:
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        return {
            "user_id": self.user_id,
            "deleting": len(self.delete_data_ids),
            "retaining": len(self.retained),
            # Named reasons, not a single number: "retained" without "why" is
            # indistinguishable from "we missed some".
            "retained_by_reason": reasons,
            "personal_memories": len(self.memories),
            "keys_revoked": self.keys,
            "producers_disabled": self.producers,
            "connections_removed": self.connections,
        }


async def plan(pool: asyncpg.Pool, org_id: str, user_id: str) -> AccountPlan:
    """Work out what goes and what stays, without changing anything."""
    rows = await pool.fetch(
        """
        SELECT d.data_id, d.access_level, c.scope AS connection_scope
        FROM data_items d
        LEFT JOIN connections c ON c.connection_id = d.connection_id
        WHERE d.owner_id = $1 AND d.org_id = $2 AND d.deleted_at IS NULL
        ORDER BY d.data_id
        """,
        user_id, org_id,
    )

    result = AccountPlan(user_id=user_id)
    for row in rows:
        if row["connection_scope"] == "shared":
            # Arrived through a connector the person set up *for the team*. The
            # data is the organisation's; the connection being theirs is an
            # implementation detail of how it got here.
            result.retained.append({
                "data_id": row["data_id"],
                "reason": "arrived through a shared connection",
            })
        elif row["access_level"] in ("org", "public"):
            # They chose to publish it to colleagues. Withdrawing it on exit
            # would delete something the org has been relying on.
            result.retained.append({
                "data_id": row["data_id"],
                "reason": f"shared with the organisation ({row['access_level']})",
            })
        else:
            result.delete_data_ids.append(row["data_id"])

    result.memories = [r["memory_id"] for r in await pool.fetch(
        """
        SELECT memory_id FROM memories
        WHERE owner_id = $1 AND project_id IN (SELECT project_id FROM projects WHERE org_id = $2)
          AND deleted_at IS NULL
        """,
        user_id, org_id,
    )]
    result.keys = await pool.fetchval(
        "SELECT count(*) FROM api_keys WHERE user_id = $1 AND org_id = $2 AND revoked_at IS NULL",
        user_id, org_id,
    )
    result.producers = await pool.fetchval(
        "SELECT count(*) FROM producers WHERE user_id = $1 AND org_id = $2 AND status <> 'disabled'",
        user_id, org_id,
    )
    result.connections = await pool.fetchval(
        "SELECT count(*) FROM connections WHERE user_id = $1 AND org_id = $2 AND scope = 'personal'",
        user_id, org_id,
    )
    return result


async def delete_account(
    pool: asyncpg.Pool,
    queue,
    principal: Principal,
    *,
    user_id: str,
    dry_run: bool = False,
    reason: str | None = None,
) -> dict:
    """Revoke access, then erase what is personal.

    A person may delete their own account. Deleting someone else's is an
    administrative act and needs the capability for one.
    """
    from .deletion import request_deletion

    if user_id != principal.user_id:
        principal.require(CONFIG_WRITE)
        if not principal.can(ADMIN) and not principal.can(CONFIG_WRITE):
            raise AccountError("deleting another account requires config:write", status=403)

    member = await pool.fetchval(
        "SELECT 1 FROM memberships WHERE user_id = $1 AND org_id = $2",
        user_id, principal.org_id,
    )
    if not member:
        raise AccountError("not a member of this organization", status=404)

    proposal = await plan(pool, principal.org_id, user_id)
    if dry_run:
        return {**proposal.summary(), "mode": "dry_run", "applied": False,
                "retained": proposal.retained[:50]}

    # Revocation first and unconditionally. The security half of offboarding
    # must not wait on the retention half -- a key that still works while a
    # deletion job grinds through a corpus is the gap that matters.
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE user_id = $1 AND org_id = $2 AND revoked_at IS NULL",
            user_id, principal.org_id,
        )
        await conn.execute(
            "UPDATE producers SET status = 'disabled' WHERE user_id = $1 AND org_id = $2",
            user_id, principal.org_id,
        )
        await conn.execute(
            "DELETE FROM connections WHERE user_id = $1 AND org_id = $2 AND scope = 'personal'",
            user_id, principal.org_id,
        )
        await conn.execute(
            "DELETE FROM memberships WHERE user_id = $1 AND org_id = $2",
            user_id, principal.org_id,
        )
        # The audit record outlives the account: it is the evidence that the
        # deletion happened, and deleting it with the person defeats the point.
        await record_audit(
            conn, principal, action="account.deleted",
            target_type="user", target_id=user_id,
            detail={**proposal.summary(), "reason": reason,
                    "requested_by": principal.user_id},
        )

    run_id = None
    if proposal.delete_data_ids:
        deletion = await request_deletion(
            pool, queue, principal,
            selector={"data_ids": proposal.delete_data_ids},
            reason=reason or f"account deletion for {user_id}",
        )
        run_id = deletion.run_id

    # Personal memories go with their owner; project memories were never theirs.
    for memory_id in proposal.memories:
        await pool.execute(
            "UPDATE memories SET deleted_at = now() WHERE memory_id = $1", memory_id
        )

    return {**proposal.summary(), "mode": "execute", "applied": True,
            "deletion_run_id": run_id, "retained": proposal.retained[:50]}
