"""Normalization -- the deterministic projection.

A projection is a **derived view, not a replacement**. `data_items` keeps the
original; this holds what normalization made of it. That separation is what lets
a schema change and the corpus be re-projected without re-running enrichment.

Two rules from the requirements shape the code:

**A failure lands the record raw with a reason and stays retryable.** It must
not reject the write -- a schema that cannot parse one record is not grounds for
losing it.

**`identifiers` is extracted here and mirrored onto `data_items`**, because
correlation joins on it and the join must not require a second table.
"""

from __future__ import annotations

import json
import re

import asyncpg

from .ids import new_id

# jmespath would be the library choice here; this covers dotted paths and array
# indexes, which is what the shipped mappings actually use. Deliberately not a
# DSL -- inventing one is how mappings become unreviewable.
_PATH = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def extract_path(payload: object, path: str):
    current = payload
    for name, index in _PATH.findall(path):
        if current is None:
            return None
        if index:
            if not isinstance(current, list) or int(index) >= len(current):
                return None
            current = current[int(index)]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(name)
    return current


async def project(
    pool: asyncpg.Pool,
    *,
    data_id: str,
    project_id: str,
    text: str,
    data_type: str | None,
) -> dict | None:
    """Apply the first matching schema for this project, if any."""
    schema = await pool.fetchrow(
        """
        SELECT target_type, version, fields, mapping FROM normalization_schemas
        WHERE (project_id = $1 OR project_id IS NULL)
          AND (mapping->>'data_type' IS NULL OR mapping->>'data_type' = $2)
        ORDER BY project_id NULLS LAST, version DESC LIMIT 1
        """,
        project_id, data_type,
    )
    if schema is None:
        return None

    try:
        payload_in = json.loads(text)
    except ValueError:
        await _record_failure(pool, data_id, schema, "content is not JSON")
        return None

    mapping = dict(schema["mapping"])
    fields = dict(schema["fields"])
    projected, missing = {}, []
    for field_name, spec in fields.items():
        source = mapping.get(field_name, field_name)
        value = extract_path(payload_in, source)
        if value is None and spec.get("required"):
            missing.append(field_name)
        if value is not None:
            projected[field_name] = value

    if missing:
        await _record_failure(
            pool, data_id, schema, f"missing required fields: {', '.join(missing)}"
        )
        return None

    identifiers = [
        str(projected[f]) for f in mapping.get("identifier_fields", []) if f in projected
    ]

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO normalized_records (data_id, target_type, schema_version,
                                            payload, identifiers, status)
            VALUES ($1, $2, $3, $4, $5, 'ok')
            ON CONFLICT (data_id) DO UPDATE SET target_type = EXCLUDED.target_type,
                schema_version = EXCLUDED.schema_version, payload = EXCLUDED.payload,
                identifiers = EXCLUDED.identifiers, status = 'ok', failure_reason = NULL
            """,
            data_id, schema["target_type"], schema["version"], projected, identifiers,
        )
        # Mirrored onto the item because correlation joins on it.
        if identifiers:
            await conn.execute(
                "UPDATE data_items SET identifiers = $2 WHERE data_id = $1",
                data_id, identifiers,
            )
    return {"target_type": schema["target_type"], "payload": projected,
            "identifiers": identifiers}


async def _record_failure(pool, data_id: str, schema, reason: str) -> None:
    """Raw, with a reason, and retryable. Never a rejected write."""
    await pool.execute(
        """
        INSERT INTO normalized_records (data_id, target_type, schema_version, status, failure_reason)
        VALUES ($1, $2, $3, 'failed', $4)
        ON CONFLICT (data_id) DO UPDATE SET status = 'failed', failure_reason = EXCLUDED.failure_reason
        """,
        data_id, schema["target_type"], schema["version"], reason,
    )


async def create_schema(
    pool: asyncpg.Pool,
    principal,
    *,
    project_id: str | None,
    target_type: str,
    fields: dict,
    mapping: dict,
) -> dict:
    from .audit import record_audit
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    version = await pool.fetchval(
        """
        SELECT coalesce(max(version), 0) + 1 FROM normalization_schemas
        WHERE project_id IS NOT DISTINCT FROM $1 AND target_type = $2
        """,
        project_id, target_type,
    )
    schema_id = new_id("sch")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO normalization_schemas (schema_id, org_id, project_id, target_type,
                                               version, fields, mapping)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            schema_id, principal.org_id, project_id, target_type, version, fields, mapping,
        )
        await record_audit(
            conn, principal, action="schema.created", project_id=project_id,
            target_type="schema", target_id=schema_id,
            # Never mutated in place: a new version, so old projections remain
            # attributable to the schema that produced them.
            detail={"target_type": target_type, "version": version},
        )
    return {"schema_id": schema_id, "target_type": target_type, "version": version}


async def list_schemas(pool: asyncpg.Pool, principal, project_id: str | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT schema_id, target_type, version, fields, mapping, created_at
        FROM normalization_schemas
        WHERE org_id = $1 AND (project_id = $2 OR project_id IS NULL)
        ORDER BY target_type, version DESC
        """,
        principal.org_id, project_id,
    )
    return [dict(r) for r in rows]
