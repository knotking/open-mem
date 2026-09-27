"""Cases -- subject correlation.

A memory answers *how long does this matter?*; a case answers *what is this
about?*. A conversation expires; a patient does not.

The load-bearing distinction here is **asserted versus inferred** membership. A
producer saying "this belongs to patient MRN-A12345" is a fact. Matching an
identifier that appears in the text is a guess, and a timeline that cannot tell
them apart is a timeline that silently includes someone else's records.
"""

from __future__ import annotations

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_access_many, record_audit
from .auth import DATA_READ, DATA_WRITE, Principal
from .ids import new_id


class CaseError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def upsert_case(
    conn,
    *,
    org_id: str,
    project_id: str,
    case_type: str,
    external_id: str,
    title: str | None = None,
) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO cases (case_id, org_id, project_id, case_type, external_id, title)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (project_id, case_type, external_id)
        DO UPDATE SET title = coalesce(EXCLUDED.title, cases.title)
        RETURNING case_id
        """,
        new_id("cas"), org_id, project_id, case_type, external_id, title,
    )
    return row["case_id"]


async def add_case_member(
    conn, case_id: str, data_id: str, *, basis: str, confidence: float | None = None,
    matched_on: str | None = None,
) -> None:
    row = await conn.fetchrow(
        """
        INSERT INTO case_members (case_id, data_id, basis, confidence, matched_on)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (case_id, data_id)
        -- An assertion outranks an inference: if a person later confirms what
        -- was guessed, the membership is upgraded, never downgraded.
        DO UPDATE SET basis = CASE WHEN case_members.basis = 'asserted'
                                   THEN 'asserted' ELSE EXCLUDED.basis END
        RETURNING basis, (xmax = 0) AS created
        """,
        case_id, data_id, basis, confidence, matched_on,
    )
    # The moment a guess becomes something to act on. In a clinical or legal
    # context that is the event people care about -- not that the association
    # existed, but that a person stood behind it.
    if row is not None and not row["created"] and basis == "asserted":
        case = await conn.fetchrow(
            "SELECT org_id, project_id, case_type FROM cases WHERE case_id = $1", case_id)
        if case is not None:
            from .alerts import emit_transition

            await emit_transition(
                conn, "case.member_promoted", org_id=case["org_id"],
                project_id=case["project_id"], data_id=data_id,
                payload={"case_id": case_id, "data_id": data_id,
                         "case_type": case["case_type"], "matched_on": matched_on},
            )


async def route_case(
    conn, *, org_id: str, project_id: str, data_id: str,
    case_type: str | None, external_id: str | None, identifiers: list[str],
) -> list[str]:
    """Attach an item to cases at write time.

    An explicit `case` on the write is asserted. Identifier matches against
    existing cases are inferred, and are recorded with what they matched on so
    a wrong correlation can be traced to its cause rather than guessed at.
    """
    attached: list[str] = []
    if case_type and external_id:
        case_id = await upsert_case(
            conn, org_id=org_id, project_id=project_id,
            case_type=case_type, external_id=external_id,
        )
        await add_case_member(conn, case_id, data_id, basis="asserted")
        attached.append(case_id)

    for identifier in identifiers:
        rows = await conn.fetch(
            """
            SELECT case_id FROM cases
            WHERE project_id = $1 AND deleted_at IS NULL
              AND ($2 = ANY(identifiers) OR external_id = $2)
            """,
            project_id, identifier,
        )
        for row in rows:
            if row["case_id"] in attached:
                continue
            await add_case_member(
                conn, row["case_id"], data_id, basis="inferred",
                confidence=0.8, matched_on=identifier,
            )
            attached.append(row["case_id"])
    return attached


async def list_cases(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT c.case_id, c.case_type, c.external_id, c.title, c.created_at,
               (SELECT count(*) FROM case_members cm
                  JOIN data_items d ON d.data_id = cm.data_id
                 WHERE cm.case_id = c.case_id AND {predicate}) AS members
        FROM cases c
        WHERE c.project_id = $1 AND c.org_id = $2 AND c.deleted_at IS NULL
        ORDER BY c.created_at DESC LIMIT 200
        """,
        project_id, org_id, user_id, principals,
    )
    return [dict(r) for r in rows]


async def timeline(pool: asyncpg.Pool, principal: Principal, case_id: str) -> dict:
    """The case's records, ordered by **event_time**.

    Not `ingested_at`. A timeline built on when we learned about something
    renders perfectly while being wrong -- a record backfilled from 2019 would
    appear at the top as though it happened today.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    case = await pool.fetchrow(
        "SELECT case_id, case_type, external_id, title, project_id FROM cases "
        "WHERE case_id = $1 AND org_id = $2 AND deleted_at IS NULL",
        case_id, org_id,
    )
    if case is None:
        raise CaseError("case not found", status=404)

    rows = await pool.fetch(
        f"""
        SELECT d.data_id, d.event_time, d.ingested_at, d.state, d.data_type,
               d.mime_type, cm.basis, cm.confidence, cm.matched_on,
               left(coalesce(d.content_text, d.extracted_text), 240) AS preview
        FROM case_members cm
        JOIN data_items d ON d.data_id = cm.data_id
        WHERE cm.case_id = $1 AND {predicate}
        ORDER BY d.event_time
        LIMIT 500
        """,
        case_id, org_id, user_id, principals,
    )
    entries = [dict(r) for r in rows]
    await record_access_many(
        pool, principal, action="case.timeline", project_id=case["project_id"],
        data_ids=[e["data_id"] for e in entries], query_id=case_id,
    )
    return {
        **dict(case),
        "entries": entries,
        "asserted": sum(1 for e in entries if e["basis"] == "asserted"),
        "inferred": sum(1 for e in entries if e["basis"] == "inferred"),
    }


async def create_case(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, case_type: str,
    external_id: str, title: str | None = None, identifiers: list[str] | None = None,
) -> dict:
    principal.require(DATA_WRITE)
    async with pool.acquire() as conn, conn.transaction():
        case_id = await upsert_case(
            conn, org_id=principal.org_id, project_id=project_id,
            case_type=case_type, external_id=external_id, title=title,
        )
        if identifiers:
            await conn.execute(
                "UPDATE cases SET identifiers = $2 WHERE case_id = $1", case_id, identifiers
            )
        await record_audit(
            conn, principal, action="case.created", project_id=project_id,
            target_type="case", target_id=case_id,
            detail={"case_type": case_type, "external_id": external_id},
        )
    return {"case_id": case_id, "case_type": case_type, "external_id": external_id}
