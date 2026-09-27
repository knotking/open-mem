"""Agent configuration -- overriding a shipped extraction prompt.

Defaults ship with the product, so an override is opting *out* of a default
rather than filling in a blank. That framing decides the API: there is always an
effective value, and `GET` returns it **with its provenance**, because "why is
this prompt being used?" is the question an operator actually has.

Changing a prompt is a versioning event. The prompt text is hashed into
`generator_version`, so an override makes every artifact the old prompt produced
detectably stale -- which is what makes `/reprocess` actionable rather than
guesswork.
"""

from __future__ import annotations

import asyncpg

from .audit import record_audit
from .auth import CONFIG_WRITE, Principal
from .extraction import EXTRACT_PURPOSE
from .ids import new_id
from .inference import generator_version
from .prompts import for_data_type


class AgentConfigError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def effective_config(
    pool: asyncpg.Pool, *, data_type: str, org_id: str, project_id: str | None
) -> dict:
    """Project beats org beats shipped, unless the org locked it."""
    rows = await pool.fetch(
        """
        SELECT org_id, project_id, prompt, flags, locked FROM agent_configs
        WHERE data_type = $1 AND (org_id = $2 OR project_id = $3)
        """,
        data_type, org_id, project_id,
    )
    org_row = next((r for r in rows if r["project_id"] is None), None)
    project_row = next((r for r in rows if r["project_id"] is not None), None)

    shipped_name, shipped_prompt = for_data_type(data_type)

    if org_row is not None and org_row["locked"]:
        # A locked org prompt is policy: an approved wording that a project
        # cannot quietly replace.
        chosen, source = org_row, "org (locked)"
    elif project_row is not None:
        chosen, source = project_row, "project"
    elif org_row is not None:
        chosen, source = org_row, "org"
    else:
        chosen, source = None, "shipped"

    prompt = chosen["prompt"] if chosen and chosen["prompt"] else shipped_prompt
    flags = dict(chosen["flags"]) if chosen else {}
    return {
        "data_type": data_type,
        "prompt": prompt,
        "flags": flags,
        "source": source,
        "shipped_prompt_name": shipped_name,
        "overridden": chosen is not None and bool(chosen["prompt"]),
        "locked": bool(org_row["locked"]) if org_row else False,
        # What this configuration hashes to. Two runs are only comparable if
        # this matches.
        "generator_version": generator_version(
            purpose=EXTRACT_PURPOSE,
            model_id="*",
            spec={"prompt": prompt, "flags": flags},
        ),
    }


async def set_config(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    data_type: str,
    prompt: str | None,
    flags: dict | None = None,
    scope: str = "project",
    project_id: str | None = None,
    lock: bool = False,
) -> dict:
    principal.require(CONFIG_WRITE)
    if scope not in ("org", "project"):
        raise AgentConfigError("scope must be org or project")
    if scope == "project" and not project_id:
        raise AgentConfigError("project_id is required at project scope")
    if lock and scope != "org":
        raise AgentConfigError("only an org can lock a prompt", status=403)

    current = await effective_config(
        pool, data_type=data_type, org_id=principal.org_id, project_id=project_id
    )
    if current["locked"] and scope == "project":
        raise AgentConfigError(
            f"the prompt for {data_type} is locked at org level", status=409
        )

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO agent_configs (config_id, org_id, project_id, data_type,
                                       prompt, flags, locked, updated_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (org_id, project_id, data_type)
            DO UPDATE SET prompt = EXCLUDED.prompt, flags = EXCLUDED.flags,
                          locked = EXCLUDED.locked, updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            """,
            new_id("agc"), principal.org_id,
            project_id if scope == "project" else None,
            data_type, prompt, flags or {}, lock, principal.user_id,
        )
        await record_audit(
            conn, principal, action="agent_config.set", project_id=project_id,
            target_type="agent_config", target_id=data_type,
            detail={"scope": scope, "locked": lock, "overridden": bool(prompt)},
        )

    updated = await effective_config(
        pool, data_type=data_type, org_id=principal.org_id, project_id=project_id
    )
    # The staleness consequence, stated rather than discovered: this is the
    # number to hand to /reprocess.
    updated["previous_generator_version"] = current["generator_version"]
    updated["artifacts_now_stale"] = (
        updated["generator_version"] != current["generator_version"]
    )
    return updated


async def test_config(
    pool: asyncpg.Pool,
    principal: Principal,
    extractor,
    *,
    data_type: str,
    sample: str,
    prompt: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Test before save.

    Editing a prompt blind and finding out across a corpus is the failure this
    prevents; the sample runs through the real extractor with the candidate
    prompt and nothing is persisted.
    """
    principal.require(CONFIG_WRITE)
    from .extraction import GeminiExtractor

    if prompt and isinstance(extractor, GeminiExtractor):
        # Temporarily swap the block in without touching stored config.
        import open_mem.prompts as prompts_module

        original = prompts_module.BY_DATA_TYPE.get(data_type)
        prompts_module.BY_DATA_TYPE[data_type] = prompt
        try:
            envelope = await extractor.extract(sample, data_type=data_type)
        finally:
            if original is None:
                prompts_module.BY_DATA_TYPE.pop(data_type, None)
            else:
                prompts_module.BY_DATA_TYPE[data_type] = original
    else:
        envelope = await extractor.extract(sample, data_type=data_type)

    return {"data_type": data_type, "envelope": envelope.model_dump(),
            "model_id": extractor.model_id, "persisted": False}
