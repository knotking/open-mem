"""Compaction: fold a memory's members into an artifact, without losing them.

**Compaction never deletes.** mem0 reconciles by overwriting and what it
replaces is gone; the temporal graph here shipped on the opposite premise -- a
claim is closed, never replaced, so `as_of` can still answer what was believed
in March. A compaction that destroyed its inputs would make `as_of` lie about
everything it touched. So members are **archived**: out of the working set,
still fetchable, still returned when asked for.

It is also deliberately about **volume, not truth**. Whether a claim is still
true is `entity_facts`, which has a validity window and evidence behind it. A
compaction that decided facts were obsolete would be a second, weaker
supersession with neither.

Two algorithms ship, and the order is the house rule rather than an accident:
`dedupe` is deterministic and costs nothing, `summarize` calls a model. Most of
what a corpus accumulates is the same record written twice, and that needs no
model to notice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from .acl import visibility_params, visibility_sql
from .auth import CONFIG_WRITE, DATA_READ, Principal
from .ids import new_id
from .telemetry import span

log = logging.getLogger(__name__)


# Served from here rather than hardcoded in a console, so the list a person
# chooses from cannot drift from the list the server will run.
ALGORITHMS: dict[str, dict] = {
    "dedupe": {
        "label": "Drop exact duplicates",
        "needs_model": False,
        "describe": "Archives members whose content is byte-identical to a newer "
                    "one, keeping the most recent. Costs nothing and is the "
                    "commonest kind of bloat -- the same record written twice.",
        "options": {},
    },
    "summarize": {
        "label": "Summarise into one artifact",
        "needs_model": True,
        "describe": "Folds members into a single summary that records every "
                    "source it drew on, then archives them. The originals stay "
                    "readable and searchable with include_archived.",
        "options": {"max_members": "how many to fold in one run (default 200)"},
    },
}


class CompactionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def validate(algorithm: str, options: dict | None) -> None:
    if algorithm not in ALGORITHMS:
        raise CompactionError(
            f"unknown algorithm {algorithm!r}; available: {', '.join(sorted(ALGORITHMS))}")
    unknown = sorted(set(options or {}) - set(ALGORITHMS[algorithm]["options"]))
    if unknown:
        raise CompactionError(f"{algorithm} takes no option(s) {', '.join(unknown)}")


# -- jobs -------------------------------------------------------------------


async def create_job(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, name: str,
    memory_id: str, algorithm: str, options: dict | None = None,
    schedule: dict | None = None,
) -> dict:
    principal.require(CONFIG_WRITE)
    validate(algorithm, options)
    owned = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE memory_id = $1 AND org_id = $2 "
        "AND project_id = $3 AND deleted_at IS NULL",
        memory_id, principal.org_id, project_id)
    if owned is None:
        raise CompactionError("memory not found in this project", status=404)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO compaction_jobs (job_id, org_id, project_id, name, memory_id,
                algorithm, options, schedule)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            RETURNING *
            """,
            new_id("cmj"), principal.org_id, project_id, name, memory_id,
            algorithm, json.dumps(options or {}),
            json.dumps(schedule or {"type": "manual"}),
        )
    except asyncpg.UniqueViolationError as exc:
        raise CompactionError(
            f"a compaction job named {name!r} already exists here", status=409) from exc
    return _public(row)


async def update_job(
    pool: asyncpg.Pool, principal: Principal, job_id: str, changes: dict,
) -> dict:
    """A change to what it would do bumps the version and drops the dry run.

    Carrying an approval forward onto a job that now archives different records
    is the mistake the crawler's dry-run gate exists to prevent, and the stakes
    are higher here: this one moves data out of the working set.
    """
    principal.require(CONFIG_WRITE)
    current = await _owned(pool, principal, job_id)
    algorithm = changes.get("algorithm", current["algorithm"])
    options = changes.get("options", _loads(current["options"]))
    memory_id = changes.get("memory_id", current["memory_id"])
    validate(algorithm, options)
    changed = (algorithm != current["algorithm"]
               or options != _loads(current["options"])
               or memory_id != current["memory_id"])
    row = await pool.fetchrow(
        """
        UPDATE compaction_jobs
           SET name = coalesce($3, name), algorithm = $4, options = $5::jsonb,
               memory_id = $6,
               schedule = coalesce($7::jsonb, schedule),
               next_due_at = CASE WHEN $7::jsonb IS NULL THEN next_due_at ELSE NULL END,
               config_version = config_version + $8,
               dry_run_version = CASE WHEN $8 = 1 THEN NULL ELSE dry_run_version END,
               enabled = CASE WHEN $8 = 1 THEN false ELSE enabled END,
               updated_at = now()
         WHERE job_id = $1 AND org_id = $2
        RETURNING *
        """,
        job_id, principal.org_id, changes.get("name"), algorithm,
        json.dumps(options), memory_id,
        json.dumps(changes["schedule"]) if "schedule" in changes else None,
        1 if changed else 0,
    )
    return _public(row)


async def set_enabled(
    pool: asyncpg.Pool, principal: Principal, job_id: str, enabled: bool,
) -> dict:
    """Enabling requires a dry run of *this* version.

    Compaction moves records out of the working set. Running one unattended that
    nobody has previewed is how a memory quietly empties.
    """
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, job_id)
    if enabled and row["dry_run_version"] != row["config_version"]:
        raise CompactionError(
            "preview this version before scheduling it: a compaction nobody has "
            "looked at is one that empties a memory quietly", status=409)
    schedule = _loads(row["schedule"])
    updated = await pool.fetchrow(
        "UPDATE compaction_jobs SET enabled = $3, next_due_at = $4, updated_at = now() "
        "WHERE job_id = $1 AND org_id = $2 RETURNING *",
        job_id, principal.org_id, enabled, _next_due(schedule) if enabled else None)
    return _public(updated)


async def list_jobs(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT j.*, m.title AS memory_title, m.type AS memory_type, m.memory_key,
               -- Through `part_of`, because that is what the run does.
               --
               -- Counted single-level, a job on a parent memory rendered as
               -- "0 members" beside a run that would consider three: the number
               -- on the card and the number the job acts on disagreed, and the
               -- card is the one people read before deciding whether to run it.
               -- DISTINCT for the same reason the run deduplicates -- a record
               -- held by both a child and its parent is one member.
               (SELECT count(DISTINCT mm.data_id) FROM memory_members mm
                 WHERE mm.memory_id IN (
                   WITH RECURSIVE contained(memory_id, depth) AS (
                       SELECT j.memory_id, 0
                       UNION
                       SELECT l.from_memory, c.depth + 1
                       FROM memory_links l JOIN contained c ON l.to_memory = c.memory_id
                       WHERE l.relation = 'part_of' AND c.depth < 12
                   )
                   SELECT memory_id FROM contained
                 )) AS members,
               (SELECT max(started_at) FROM compaction_runs r WHERE r.job_id = j.job_id)
                 AS last_run_at,
               (SELECT coalesce(sum(archived), 0) FROM compaction_runs r
                 WHERE r.job_id = j.job_id AND r.mode = 'live') AS archived_total
          FROM compaction_jobs j
          JOIN memories m ON m.memory_id = j.memory_id
         WHERE j.project_id = $1 AND j.org_id = $2 AND j.deleted_at IS NULL
         ORDER BY j.created_at DESC
        """,
        project_id, principal.org_id,
    )
    return [_public(r) for r in rows]


async def delete_job(pool: asyncpg.Pool, principal: Principal, job_id: str) -> dict:
    principal.require(CONFIG_WRITE)
    await _owned(pool, principal, job_id)
    await pool.execute(
        "UPDATE compaction_jobs SET deleted_at = now(), enabled = false WHERE job_id = $1",
        job_id)
    return {"job_id": job_id, "deleted": True}


async def runs_for(
    pool: asyncpg.Pool, principal: Principal, job_id: str, limit: int = 20,
) -> list[dict]:
    principal.require(DATA_READ)
    await _owned(pool, principal, job_id)
    rows = await pool.fetch(
        "SELECT * FROM compaction_runs WHERE job_id = $1 "
        "ORDER BY started_at DESC LIMIT $2", job_id, limit)
    return [dict(r) for r in rows]


def _next_due(schedule: dict):
    if schedule.get("type") == "interval":
        return datetime.now(timezone.utc) + timedelta(
            seconds=int(schedule.get("every_seconds", 86_400)))
    return None


async def _owned(pool: asyncpg.Pool, principal: Principal, job_id: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM compaction_jobs WHERE job_id = $1 AND org_id = $2 "
        "AND deleted_at IS NULL", job_id, principal.org_id)
    if row is None:
        raise CompactionError("compaction job not found", status=404)
    return row


def _loads(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _public(row) -> dict:
    d = dict(row)
    for key in ("options", "schedule"):
        if key in d:
            d[key] = _loads(d[key])
    d.pop("deleted_at", None)
    return d


# -- running ----------------------------------------------------------------


async def run(
    pool: asyncpg.Pool, principal: Principal | None, *, job_id: str | None = None,
    memory_id: str | None = None, algorithm: str | None = None,
    options: dict | None = None, mode: str = "live", trigger: str = "manual",
    extractor=None,
) -> dict:
    """One pass. `mode='dry'` reports what a live run would do and writes nothing.

    Same code path either way, so what a preview reports is what would actually
    happen -- the crawler's discipline, and it matters more here because the
    live version moves records out of the working set.
    """
    job = None
    if job_id:
        job = await pool.fetchrow(
            "SELECT * FROM compaction_jobs WHERE job_id = $1 AND deleted_at IS NULL", job_id)
        if job is None:
            raise CompactionError("compaction job not found", status=404)
        memory_id, algorithm = job["memory_id"], job["algorithm"]
        options = _loads(job["options"])
    if not memory_id or not algorithm:
        raise CompactionError("a memory and an algorithm are required")
    validate(algorithm, options)

    if job is not None and mode == "live" and job["overlap"] == "skip":
        live = await pool.fetchval(
            "SELECT run_id FROM compaction_runs WHERE job_id = $1 AND status = 'running' "
            "LIMIT 1", job_id)
        if live:
            return {"job_id": job_id, "status": "skipped", "run_id": None}

    memory = await pool.fetchrow(
        "SELECT org_id, project_id FROM memories WHERE memory_id = $1", memory_id)
    if memory is None:
        raise CompactionError("memory not found", status=404)

    run_id = new_id("cmr")
    await pool.execute(
        """
        INSERT INTO compaction_runs (run_id, job_id, memory_id, org_id, algorithm,
            config_version, mode, trigger)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        run_id, job_id, memory_id, memory["org_id"], algorithm,
        job["config_version"] if job else None, mode, trigger,
    )

    try:
        with span("compaction.run", algorithm=algorithm, mode=mode):
            members = await _members(pool, principal, memory_id)
            before = sum(m["content_chars"] or 0 for m in members)

            if algorithm == "dedupe":
                result = await _dedupe(pool, members, mode=mode, run_id=run_id)
            else:
                result = await _summarize(
                    pool, members, memory_id=memory_id, org_id=memory["org_id"],
                    project_id=memory["project_id"], mode=mode, run_id=run_id,
                    extractor=extractor, options=options or {},
                )

            after = before - result["bytes_freed"]
            await pool.execute(
                """
                UPDATE compaction_runs SET status = 'completed', considered = $2,
                       archived = $3, artifacts = $4, bytes_before = $5,
                       bytes_after = $6, model_calls = $7, finished_at = now()
                 WHERE run_id = $1
                """,
                run_id, len(members), result["archived"], result["artifacts"],
                before, after, result["model_calls"],
            )
    except Exception as exc:
        await pool.execute(
            "UPDATE compaction_runs SET status = 'failed', error = $2, finished_at = now() "
            "WHERE run_id = $1", run_id, str(exc)[:500])
        raise

    if mode == "live":
        # Recomputed, so it is current again. A dry run must not clear it: what
        # it reports is what *would* happen, and a preview that marked the
        # rollup fresh would be a preview with a side effect.
        from .memories import clear_stale

        async with pool.acquire() as conn:
            await clear_stale(conn, memory_id)
    if job is not None and mode == "live":
        await pool.execute(
            "UPDATE compaction_jobs SET next_due_at = $2 WHERE job_id = $1",
            job_id, _next_due(_loads(job["schedule"])))
    if job is not None and mode == "dry":
        await pool.execute(
            "UPDATE compaction_jobs SET dry_run_version = config_version WHERE job_id = $1",
            job_id)

    return {"run_id": run_id, "job_id": job_id, "status": "completed", "mode": mode,
            "considered": len(members), "bytes_before": before, "bytes_after": after,
            **result}


async def _members(pool, principal, memory_id: str) -> list[dict]:
    """Live members only, **through the hierarchy**.

    An archived item is already out of the working set, and compacting it twice
    would report work that did not happen.

    A `part_of` parent has no members of its own -- its members *are* its
    children's -- so a compaction of one used to consider nothing and report a
    successful run over zero records. Reading through the hierarchy is the same
    walk the alert scope makes, for the same reason: the container means what it
    says it means, at the point of use.

    The ACL follows for free and is the part worth stating. `_summarize` takes
    the strictest access level among the sources it read, so a rollup over four
    child memories is visible only to whoever can read **all four**. That will
    surprise somebody, and the alternative is a summary that says out loud what
    one of its sources was restricted about.
    """
    from .memories import contained_memories

    scope = await contained_memories(pool, memory_id)
    if principal is not None:
        org_id, user_id, principals = visibility_params(principal)
        predicate = visibility_sql("d", 1, 2, 3)
        rows = await pool.fetch(
            f"""
            SELECT d.data_id, d.external_id, d.checksum, d.content_text,
                   d.access_level, d.shared_with, d.owner_id, d.tags,
                   length(coalesce(d.content_text, '')) AS content_chars, d.created_at
              FROM memory_members mm JOIN data_items d ON d.data_id = mm.data_id
             WHERE mm.memory_id = ANY($4::text[]) AND d.archived_at IS NULL
               AND {predicate}
             ORDER BY d.created_at
            """,
            org_id, user_id, principals, scope,
        )
    else:
        # The scheduled path has no caller. It compacts the memory's members as
        # the memory's owner would see them -- the job was created by somebody
        # who could see them, and `config_version` records that decision.
        rows = await pool.fetch(
            """
            SELECT d.data_id, d.external_id, d.checksum, d.content_text,
                   d.access_level, d.shared_with, d.owner_id, d.tags,
                   length(coalesce(d.content_text, '')) AS content_chars, d.created_at
              FROM memory_members mm JOIN data_items d ON d.data_id = mm.data_id
             WHERE mm.memory_id = ANY($1::text[]) AND d.archived_at IS NULL
               AND d.deleted_at IS NULL
             ORDER BY d.created_at
            """,
            scope,
        )
    # One row per item, not one per membership: a record held by both a child
    # and its parent would otherwise be counted twice, folded twice, and
    # reported as two records freed.
    seen, unique = set(), []
    for row in rows:
        if row["data_id"] in seen:
            continue
        seen.add(row["data_id"])
        unique.append(dict(row))
    return unique


def _owner_of(members: list[dict], level: str) -> str | None:
    """Whose record set the level this artifact inherited.

    Precise rather than convenient: a private artifact should be readable by
    exactly the person who could read the private source it came from, and
    attributing it to whoever happened to run the job would hand them a record
    they may not have been able to read.
    """
    for member in members:
        if member.get("access_level") == level and member.get("owner_id"):
            return member["owner_id"]
    return next((m.get("owner_id") for m in members if m.get("owner_id")), None)


async def _archive(pool, data_ids: list[str], run_id: str) -> None:
    """Out of the working set, not gone. Retrieval excludes these by default and
    returns them on `include_archived` -- which is the whole user-visible effect
    of compaction, and the reason it is safe to run unattended."""
    await pool.execute(
        "UPDATE data_items SET archived_at = now(), archived_by = $2 "
        "WHERE data_id = ANY($1::text[])", data_ids, run_id)


async def _dedupe(pool, members: list[dict], *, mode: str, run_id: str) -> dict:
    """Byte-identical content, keeping the newest. No model, no judgement.

    Deterministic on the checksum the write path already computes, so this is
    the cheap half of the problem and it is most of it: a re-crawl and a
    re-import produce identical records constantly.
    """
    seen: dict[str, dict] = {}
    doomed: list[dict] = []
    for m in sorted(members, key=lambda x: x["created_at"], reverse=True):
        key = m["checksum"] or f"len:{m['content_chars']}:{(m['content_text'] or '')[:200]}"
        if key in seen:
            doomed.append(m)
        else:
            seen[key] = m
    if mode == "live" and doomed:
        await _archive(pool, [d["data_id"] for d in doomed], run_id)
    return {"archived": len(doomed), "artifacts": 0, "model_calls": 0,
            "bytes_freed": sum(d["content_chars"] or 0 for d in doomed),
            "samples": [{"data_id": d["data_id"], "external_id": d["external_id"],
                         "chars": d["content_chars"]} for d in doomed[:25]]}


async def _summarize(
    pool, members: list[dict], *, memory_id: str, org_id: str, project_id: str,
    mode: str, run_id: str, extractor, options: dict,
) -> dict:
    """One artifact over the members, recording every source it drew on.

    The source list carries **span offsets**, which is what lets a citation open
    its source at the sentence. Without them a summary can name what it read and
    not point into it, and every citation in a compacted memory quietly degrades
    to a document-level reference -- which reads as working.
    """
    cap = int(options.get("max_members", 200))
    batch = members[:cap]
    if len(batch) < 2:
        return {"archived": 0, "artifacts": 0, "model_calls": 0, "bytes_freed": 0,
                "samples": [], "note": "nothing to fold: a summary of one record "
                                       "is the record"}
    if extractor is None:
        raise CompactionError(
            "no extraction model is configured; use the dedupe algorithm, which "
            "needs none", status=503)

    joined, offsets, cursor = [], [], 0
    for m in batch:
        text = (m["content_text"] or "").strip()
        joined.append(text)
        offsets.append((m["data_id"], cursor, cursor + len(text)))
        cursor += len(text) + 2

    if mode == "dry":
        return {"archived": 0, "artifacts": 0, "model_calls": 0,
                "bytes_freed": sum(m["content_chars"] or 0 for m in batch),
                "samples": [{"data_id": m["data_id"], "external_id": m["external_id"],
                             "chars": m["content_chars"]} for m in batch[:25]],
                "note": f"would fold {len(batch)} members into one summary"}

    envelope = await extractor.extract(
        "\n\n".join(joined)[:200_000], data_type="document_text")

    # The artifact takes the ACL of its most restrictive source. A summary
    # spanning a private and two org records is private, or compaction becomes a
    # way to widen visibility by summarising -- which is the leak `acl.strictest`
    # exists to prevent everywhere else.
    from .acl import Acl, strictest

    acl = strictest([
        Acl(m["access_level"], _shared(m["shared_with"])) for m in batch
    ])
    generator = await _register_generator(pool, extractor)
    artifact_id = new_id("art")
    async with pool.acquire() as conn, conn.transaction():
        # One transaction: an artifact whose sources were not recorded is
        # unerasable, and an archive with no artifact is data loss.
        await conn.execute(
            """
            INSERT INTO artifacts (artifact_id, org_id, project_id, kind, title,
                summary, model_id, generator_version, served_by_model,
                access_level, shared_with, owner_id)
            VALUES ($1, $2, $3, 'compaction', $4, $5, $6, $7, $6, $8, $9::jsonb, $10)
            """,
            artifact_id, org_id, project_id,
            (getattr(envelope, "title", None) or "Compacted memory")[:200],
            getattr(envelope, "summary", None) or "",
            getattr(extractor, "model_id", None) or "unknown",
            generator, acl.access_level, json.dumps(acl.shared_with),
            # Owned by whoever owns the record whose ACL this inherited.
            #
            # Without an owner a `private` artifact is readable by nobody: the
            # predicate is `access_level = 'private' AND owner_id = $user`, and
            # NULL matches no user. Every compaction summary over private
            # records has been invisible to everyone including the person who
            # compacted them -- and invisible is exactly how a working summary
            # and a missing one look the same.
            _owner_of(batch, acl.access_level),
        )
        for data_id, start, end in offsets:
            await conn.execute(
                "INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                artifact_id, data_id, start, end)
        await conn.execute(
            "UPDATE data_items SET archived_at = now(), archived_by = $2 "
            "WHERE data_id = ANY($1::text[])", [m["data_id"] for m in batch], run_id)

    return {"archived": len(batch), "artifacts": 1, "model_calls": 1,
            "bytes_freed": sum(m["content_chars"] or 0 for m in batch),
            "artifact_id": artifact_id,
            "samples": [{"data_id": m["data_id"], "external_id": m["external_id"],
                         "chars": m["content_chars"]} for m in batch[:25]]}


async def tick(pool: asyncpg.Pool, *, limit: int = 10, extractor=None) -> dict:
    """Due jobs, one pass. Driven by the same sweep the alerts use."""
    due = await pool.fetch(
        """
        SELECT job_id FROM compaction_jobs
         WHERE enabled AND deleted_at IS NULL
           AND next_due_at IS NOT NULL AND next_due_at <= now()
         ORDER BY next_due_at LIMIT $1
        """,
        limit,
    )
    return {"ran": [
        await run(pool, None, job_id=r["job_id"], trigger="schedule", extractor=extractor)
        for r in due
    ]}


def _shared(value) -> list[str]:
    parsed = json.loads(value) if isinstance(value, str) else (value or [])
    return list(parsed)


async def _register_generator(pool, extractor) -> str:
    """Compaction is a generator like any other.

    Artifacts reference `generators` and staleness is a join against it, so a
    summary has to name the configuration that produced it -- which is also what
    makes changing the prompt detectably invalidate every summary it wrote.
    """
    from .extraction import EXTRACT_PURPOSE
    from .inference import generator_version

    spec = {"envelope": "core-v1", "purpose": "compaction"}
    version = generator_version(
        purpose=EXTRACT_PURPOSE, model_id=extractor.model_id, spec=spec)
    await pool.execute(
        "INSERT INTO generators (generator_version, purpose, model_id, spec) "
        "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (generator_version) DO NOTHING",
        version, EXTRACT_PURPOSE, extractor.model_id, json.dumps(spec))
    return version
