"""Deriving an artifact from a memory, with a named generator.

A memory supported exactly one derived artifact: a summary, reachable only
through `compress`, which also archived everything it read. So a study guide, a
flashcard deck, an obligations extract, a customer briefing and a timeline
digest — the same operation with a different output schema — each faced
becoming a bespoke endpoint.

**The registry this needs already existed.** `generator_version` is a hash of
the prompt, model and schema against an immutable generators table, built for
item-level enrichment. Extending it from an item to a memory's member set is a
scope change rather than a new mechanism, and it brings three properties along
untouched: an artifact records what produced it, a membership change
invalidates it, and its source list makes erasure cascade correctly.

**Deriving and compressing are now two things, and that is the point.**
Deriving produces an artifact. Compression is the *policy* of archiving the
originals behind one — it goes on being available, and it stops being the only
way to get an artifact at all. A study guide that archived the course material
it was built from would be a study guide that ate the course.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from .acl import Acl, strictest
from .audit import record_audit
from .auth import DATA_READ, DATA_WRITE, Principal
from .ids import new_id
from .telemetry import span

log = logging.getLogger(__name__)


class DeriveError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# What can be made from a set of records.
#
# A generator is a prompt and a name. Nothing else is needed, because the
# extraction seam already accepts a per-request prompt and the fingerprint
# already hashes it -- so adding one is configuration, and each is
# reproducible, invalidatable and erasable the day it is added.
#
# `archives` is the line between deriving and compressing. Only `summary` sets
# it, and only when it is asked to: a flashcard deck that archived the material
# it was built from would eat the course.
GENERATORS: dict[str, dict] = {
    "summary": {
        "label": "Summary",
        "describe": "One summary over the members, recording every source it drew on. "
                    "What `compress` produced, now reachable without archiving anything.",
        "prompt": None,          # the shipped extraction prompt
        "archivable": True,
    },
    "study_guide": {
        "label": "Study guide",
        "describe": "The concepts these records teach, ordered so somebody could learn "
                    "from them, with what to read for each.",
        "prompt": (
            "You are building a study guide from the material below. Produce an ordered "
            "list of the concepts it teaches, from foundational to advanced. For each, "
            "state it in one sentence and name what in the material covers it. Do not "
            "invent concepts the material does not contain, and say so if it covers "
            "less than it appears to."
        ),
        "archivable": False,
    },
    "flashcards": {
        "label": "Flashcards",
        "describe": "Question and answer pairs drawn only from what the records actually "
                    "say -- a card whose answer is not in the material is worse than no card.",
        "prompt": (
            "Produce question-and-answer pairs from the material below, one per fact worth "
            "remembering. Every answer must be supported by the material as written. Prefer "
            "fewer cards that are exactly right to more that are approximately right, and "
            "produce none at all rather than guessing."
        ),
        "archivable": False,
    },
    "obligations": {
        "label": "Obligations",
        "describe": "Who must do what, by when. Extracted rather than inferred: an "
                    "obligation nobody wrote down is not an obligation.",
        "prompt": (
            "List the obligations in the material below: who is obliged, what they must "
            "do, and by when. Quote the wording each one rests on. Do not infer an "
            "obligation from silence or from what would be reasonable -- if the material "
            "does not state it, it is not there."
        ),
        "archivable": False,
    },
    "briefing": {
        "label": "Briefing",
        "describe": "What somebody walking into a conversation about these records needs "
                    "to know, and what is still open.",
        "prompt": (
            "Brief somebody who is about to discuss the material below and has not read "
            "it. State what has happened, what was decided, what is still open, and what "
            "the other party is likely to raise. Mark anything uncertain as uncertain "
            "rather than smoothing it."
        ),
        "archivable": False,
    },
    "timeline": {
        "label": "Timeline",
        "describe": "What happened in order, with the date each event is stated to have "
                    "occurred -- not the order the records arrived in.",
        "prompt": (
            "Produce a timeline from the material below: each event, the date it is stated "
            "to have happened, and one sentence of what occurred. Order by the stated date "
            "rather than by the order the records appear. Where a date is absent or "
            "ambiguous, say so instead of estimating."
        ),
        "archivable": False,
    },
}


def validate(generator: str) -> dict:
    if generator not in GENERATORS:
        raise DeriveError(
            f"unknown generator {generator!r}; available: {', '.join(sorted(GENERATORS))}")
    return GENERATORS[generator]


async def register(pool: asyncpg.Pool, generator: str, extractor) -> str:
    """The fingerprint this artifact was produced under.

    The **prompt is in the hash**, which is what makes changing a generator
    detectably invalidate everything it wrote. Without that, editing the study
    guide prompt would leave a corpus of guides that claim to be current and
    were produced by wording nobody can recover.
    """
    from .extraction import EXTRACT_PURPOSE
    from .inference import generator_version

    spec = {"envelope": "core-v1", "purpose": "derive", "generator": generator,
            "prompt": GENERATORS[generator]["prompt"]}
    version = generator_version(
        purpose=EXTRACT_PURPOSE, model_id=extractor.model_id, spec=spec)
    await pool.execute(
        "INSERT INTO generators (generator_version, purpose, model_id, spec) "
        "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (generator_version) DO NOTHING",
        version, EXTRACT_PURPOSE, extractor.model_id, json.dumps(spec))
    return version


async def derive(
    pool: asyncpg.Pool, principal: Principal, memory_id: str, *,
    generator: str = "summary", extractor=None, archive: bool = False,
    max_members: int = 200, dry_run: bool = False,
) -> dict:
    """Make one artifact from a memory's members.

    Reads **through the hierarchy**, like everything else that reads a memory:
    a `part_of` parent's members are its children's, so deriving from a
    programme covers its workstreams.

    `archive` is what compression adds, and it is refused for a generator that
    is not archivable — folding the originals away behind a flashcard deck
    would leave the deck as the only remaining copy of the course.
    """
    from .compaction import _members
    from .memories import MemoryError as MemErr

    principal.require(DATA_WRITE)
    spec = validate(generator)
    if archive and not spec["archivable"]:
        raise DeriveError(
            f"{generator!r} cannot archive what it reads: the artifact would become the "
            "only remaining copy of the material it was built from", status=400)

    memory = await pool.fetchrow(
        "SELECT org_id, project_id FROM memories WHERE memory_id = $1 AND deleted_at IS NULL",
        memory_id)
    if memory is None or memory["org_id"] != principal.org_id:
        raise MemErr("memory not found", status=404)

    members = (await _members(pool, principal, memory_id))[:max_members]
    if not members:
        return {"memory_id": memory_id, "generator": generator, "artifacts": 0,
                "members": 0, "note": "nothing in this memory that you can see"}

    joined, offsets, cursor = [], [], 0
    for m in members:
        text = (m["content_text"] or "").strip()
        joined.append(text)
        # Span offsets, so a citation opens at the sentence. Without them an
        # artifact can name what it read and not point into it, and every
        # citation degrades to a document-level reference -- which reads as
        # working.
        offsets.append((m["data_id"], cursor, cursor + len(text)))
        cursor += len(text) + 2

    if dry_run:
        return {
            "memory_id": memory_id, "generator": generator, "members": len(members),
            "would_archive": len(members) if archive else 0,
            "chars": cursor,
            "samples": [{"data_id": m["data_id"], "external_id": m["external_id"],
                         "chars": m["content_chars"]} for m in members[:25]],
            "applied": False,
        }

    if extractor is None:
        raise DeriveError(
            "no extraction model is configured, and every generator here needs one",
            status=503)

    with span("derive", generator=generator, memory_id=memory_id):
        envelope = await extractor.extract(
            "\n\n".join(joined)[:200_000], data_type="document_text",
            prompt=spec["prompt"])

    # The strictest ACL among the sources, which is the rule everywhere a
    # derived thing spans several records: a study guide over a private and two
    # org documents is private, or deriving becomes a way to widen visibility.
    acl = strictest([Acl(m["access_level"], _shared(m["shared_with"])) for m in members])
    version = await register(pool, generator, extractor)
    artifact_id = new_id("art")

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO artifacts (artifact_id, org_id, project_id, kind, title,
                summary, model_id, generator_version, served_by_model,
                access_level, shared_with, owner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $7, $9, $10::jsonb, $11)
            """,
            artifact_id, memory["org_id"], memory["project_id"], generator,
            (getattr(envelope, "title", None) or GENERATORS[generator]["label"])[:200],
            getattr(envelope, "summary", None) or "",
            getattr(extractor, "model_id", None) or "unknown",
            version, acl.access_level, json.dumps(acl.shared_with),
            # Owned by whoever owns the record whose ACL this inherited. A
            # `private` artifact with no owner is readable by nobody -- the
            # predicate is `owner_id = $user`, and NULL matches none -- so it
            # would exist, be correct, and be invisible to everyone including
            # the person who asked for it.
            _owner_of(members, acl.access_level),
        )
        for data_id, start, end in offsets:
            await conn.execute(
                "INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                artifact_id, data_id, start, end)
        if archive:
            await conn.execute(
                "UPDATE data_items SET archived_at = now(), archived_by = $2 "
                "WHERE data_id = ANY($1::text[])",
                [m["data_id"] for m in members], artifact_id)
        # The rollup is current again, whatever marked it stale.
        from .memories import clear_stale

        await clear_stale(conn, memory_id)
        await record_audit(
            conn, principal, action="memory.derived", project_id=memory["project_id"],
            target_type="memory", target_id=memory_id,
            detail={"generator": generator, "artifact_id": artifact_id,
                    "members": len(members), "archived": len(members) if archive else 0},
        )

    return {"memory_id": memory_id, "generator": generator, "artifact_id": artifact_id,
            "artifacts": 1, "members": len(members),
            "archived": len(members) if archive else 0,
            "generator_version": version, "access_level": acl.access_level,
            "applied": True}


def _owner_of(members: list[dict], level: str) -> str | None:
    """Whose record set the level this artifact inherited."""
    for member in members:
        if member.get("access_level") == level and member.get("owner_id"):
            return member["owner_id"]
    return next((m.get("owner_id") for m in members if m.get("owner_id")), None)


def _shared(value) -> list[str]:
    if isinstance(value, str):
        return json.loads(value)
    return list(value or [])


async def artifacts_for(
    pool: asyncpg.Pool, principal: Principal, memory_id: str
) -> list[dict]:
    """What has been derived from this memory, newest first.

    ACL-filtered on the artifact rather than the memory: an artifact takes the
    strictest level among its sources, so it can legitimately be narrower than
    the container it was built from.
    """
    from .acl import visibility_params, visibility_sql

    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT a.artifact_id, a.kind, a.title, a.summary, a.model_id,
               a.generator_version, a.access_level, a.created_at,
               (SELECT count(*) FROM artifact_sources s2 WHERE s2.artifact_id = a.artifact_id)
                 AS sources,
               (SELECT g.generator_version <> a.generator_version FROM generators g
                 WHERE g.purpose = 'extraction' ORDER BY g.generator_version LIMIT 1)
                 AS maybe_stale
          FROM artifacts a
          JOIN artifact_sources s ON s.artifact_id = a.artifact_id
          JOIN memory_members mm ON mm.data_id = s.data_id
         WHERE mm.memory_id = $4 AND {visibility_sql("a", 1, 2, 3)}
         ORDER BY a.created_at DESC
         LIMIT 50
        """,
        org_id, user_id, principals, memory_id,
    )
    return [dict(r) for r in rows]
