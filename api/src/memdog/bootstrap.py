"""Creating a tenant.

The control-plane endpoints (`/organizations`, `/projects`, `/users/me/api-keys`)
belong to a later slice. Until they exist this is how an org, a project, a
member, a connection, a producer and a credential come into being -- and it is
deliberately the same sequence those endpoints will perform, so the tests
exercise the real object graph rather than fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, issue_key
from .memories import ensure_shipped_types
from .ids import new_id


@dataclass(frozen=True)
class Tenant:
    org_id: str
    project_id: str
    user_id: str
    producer_id: str
    connection_id: str | None
    api_key: str


async def create_user(pool: asyncpg.Pool, email: str, display_name: str = "") -> str:
    user_id = new_id("usr")
    await pool.execute(
        "INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $3)",
        user_id,
        email,
        display_name or email,
    )
    # Even a locally-created user gets an identity row. Making `identities` the
    # permanent design rather than migration scaffolding is what turns adding a
    # hosted IdP later into inserting rows.
    await pool.execute(
        """
        INSERT INTO identities (identity_id, user_id, provider, external_id)
        VALUES ($1, $2, 'local', $3)
        """,
        new_id("idn"),
        user_id,
        email,
    )
    return user_id


class AlreadyBootstrapped(RuntimeError):
    """There is already somebody here."""


async def refuse_if_occupied(pool: asyncpg.Pool) -> None:
    """Bootstrap is a one-time operation, not a standing capability.

    Registration is invite-only, which has a chicken-and-egg problem: somebody
    must exist before anyone can be invited, and this is the exception that
    solves it. An exception that can be taken twice is not an exception -- it is
    an unauthenticated account-creation endpoint wearing an operations script's
    clothes, reachable by anyone who can run the command in the deployment.

    So it refuses once anybody exists, and says so rather than silently doing
    nothing: an operator who ran it twice needs to know which of the two
    credentials is real.
    """
    existing = await pool.fetchval("SELECT count(*) FROM users")
    if existing:
        raise AlreadyBootstrapped(
            f"{existing} user(s) already exist. Bootstrap creates the first "
            "admin and only the first; everyone after them arrives by invite "
            "(POST /api/v1/invites)."
        )


async def bootstrap_tenant(
    pool: asyncpg.Pool,
    *,
    org_name: str = "acme",
    project_name: str = "default",
    email: str = "owner@example.com",
    connection_scope: str | None = "personal",
    capabilities: list[str] | None = None,
    producer_type: str = "client",
) -> Tenant:
    org_id, project_id = new_id("org"), new_id("prj")
    await pool.execute(
        "INSERT INTO organizations (org_id, name) VALUES ($1, $2)", org_id, org_name
    )
    await pool.execute(
        "INSERT INTO projects (project_id, org_id, name) VALUES ($1, $2, $3)",
        project_id,
        org_id,
        project_name,
    )
    await ensure_shipped_types(pool, project_id, org_id)
    user_id = await create_user(pool, email)
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'owner')",
        user_id,
        org_id,
    )

    connection_id = None
    if connection_scope is not None:
        connection_id = new_id("conn")
        await pool.execute(
            """
            INSERT INTO connections (connection_id, org_id, project_id, user_id, provider, scope)
            VALUES ($1, $2, $3, $4, 'manual', $5)
            """,
            connection_id,
            org_id,
            project_id,
            user_id,
            connection_scope,
        )

    producer_id = new_id({"client": "key", "webhook": "whk", "crawler": "crw",
                          "upload": "upl", "agent": "key"}[producer_type])
    await pool.execute(
        """
        INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                               connection_id, status, inbound_auth)
        VALUES ($1, $2, $3, $4, $5, $6, 'enabled', 'none')
        """,
        producer_id,
        producer_type,
        user_id,
        org_id,
        project_id,
        connection_id,
    )

    token = await issue_key(
        pool,
        user_id=user_id,
        org_id=org_id,
        project_id=project_id,
        # The org owner's own key. Config:write but deliberately not admin:* --
        # so the admin-only paths are still exercised rather than waved through.
        capabilities=capabilities or [DATA_READ, DATA_WRITE, CONFIG_WRITE],
        name="bootstrap",
    )
    return Tenant(org_id, project_id, user_id, producer_id, connection_id, token)
