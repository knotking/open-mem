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


# A member carrying this is stored with the memory and never read into an
# artifact. One tag, because the moment there are two the rule stops being
# legible from the record itself.
SKIP_TAG = "derive:skip"

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
    # Change detection over a checkpoint timeline. Two generators because it is
    # two questions, and running them as one is the version that does not work:
    # a single call given two documents and asked what changed writes a summary
    # of both.
    #
    # `checkpoint_state` reads ONE record. The fixed order in the prompt is the
    # load-bearing part -- two runs over the same content have to fill the same
    # slots, or the comparison below reports rephrasing as change and does it
    # confidently.
    "checkpoint_state": {
        "data_type": "checkpoint_state",
        "label": "Checkpoint state",
        "describe": "What one record in a timeline says, as a fixed list of "
                    "observations -- the comparable form the change check reads.",
        "prompt": (
            "Describe the state of the record below as a list of short, factual, "
            "self-contained observations. Cover them in this order and omit a "
            "heading entirely if the record says nothing about it: what this is "
            "and what it identifies; its status or stage; quantities, amounts "
            "and counts; dates and deadlines; the people and organisations "
            "involved and their roles; what is unresolved or outstanding. "
            "One fact per observation, stated the same way you would state it "
            "about any other record of this kind. Do not summarise, do not "
            "interpret, and do not record anything the record does not say."
        ),
        "archivable": False,
    },
    # `checkpoint_change` reads TWO STATES, never the two records. Handing a
    # generator its raw source is what made all four repository reports an echo
    # of their own input, and the fix there -- compress first, compare the
    # compression -- is the shape this is built in from the start.
    "checkpoint_change": {
        "data_type": "checkpoint_change",
        "label": "What changed",
        "describe": "What moved between one checkpoint and the one before it, "
                    "with the previous and current values.",
        "prompt": (
            "Below are two descriptions of the same subject at two points in "
            "time, labelled EARLIER and LATER. Report only what differs between "
            "them: what appeared, what disappeared, and what changed value. For "
            "a changed value give both the earlier and the later one. "
            "Wording is not change -- if the two descriptions state the same "
            "fact differently, that is not a change and must not be reported. "
            "If nothing differs, return an empty list; that is a complete and "
            "correct answer, and inventing a change to avoid an empty one is "
            "the worst thing you can do here. "
            # Bounded, because this generator once spent its whole output
            # budget repeating one word inside `before`. The schema now carries
            # a length and a description; saying it here as well costs nothing
            # and the failure it prevents cost a deploy.
            "Report at most 20 changes. `before` and `after` must each be the "
            "value itself, copied as it appears and under twenty words -- never "
            "reasoning, never a restatement of the question, never a list. Put "
            "the explanation in `statement`, in one sentence."
        ),
        "archivable": False,
    },
    # The four repository reports. They read a snapshot's members -- the code
    # graph `graphify` produced, the manifests, the OSV result, and the bounded
    # file set the job selected -- and never the repository, which exceeds every
    # ceiling in `docs/limit.md` by orders of magnitude.
    #
    # All four share one rule, stated in every prompt because it is the failure
    # that matters: **a finding that cannot name a file is not a finding.** An
    # unlocatable claim about somebody's codebase reads exactly like a located
    # one and cannot be checked, which is the code-review equivalent of the
    # hallucinated edge the graph vocabulary is careful about.
    "repo_design": {
        "data_type": "code_review",
        "label": "Design",
        "describe": "How the codebase is arranged -- its layers, the seams between "
                    "them, and where the arrangement is not what it appears to be.",
        "prompt": (
            "You are describing the design of a codebase from the graph and files "
            "below. Cover: what the major components are and what each is "
            "responsible for; how they depend on one another; where the boundaries "
            "are clean and where they leak. Name every component by its actual path "
            "or module name from the material. Where the structure suggests an "
            "intended layering that the dependencies violate, say so and name the "
            "edge that violates it. Do not praise or grade the design; describe it. "
            "If the material does not show something, say it is not visible here "
            "rather than inferring it from convention."
            " Keep the whole report under 400 words and at most eight findings, "
            "ordered by what matters most. Brevity is not a style preference "
            "here: the envelope is JSON, and an answer that runs long is cut "
            "off mid-string and parses as nothing at all -- a ninth finding "
            "costs the other eight."
        ),
        "archivable": False,
    },
    "repo_quality": {
        "data_type": "code_review",
        "label": "Code quality",
        "describe": "Duplication, oversized modules, dead code and the shape of the "
                    "test coverage -- each pointing at a file.",
        "prompt": (
            "You are assessing code quality from the graph and files below. Report "
            "only what the material supports: modules that are far larger or more "
            "connected than their peers; apparent duplication; symbols nothing "
            "references; areas with no visible test coverage. For each observation "
            "name the file and, where you have it, the symbol. Order by how much "
            "the observation would matter to someone maintaining this code. Do not "
            "report style preferences, and do not produce a score -- a number "
            "invites comparison between codebases this analysis cannot support."
            " Keep the whole report under 400 words and at most eight findings, "
            "ordered by what matters most. Brevity is not a style preference "
            "here: the envelope is JSON, and an answer that runs long is cut "
            "off mid-string and parses as nothing at all -- a ninth finding "
            "costs the other eight."
        ),
        "archivable": False,
    },
    "repo_bugs": {
        "data_type": "code_review",
        "label": "Functional bugs",
        "describe": "Specific defects, each located at a file and symbol, with the "
                    "input or state that would trigger it.",
        "prompt": (
            "You are looking for functional bugs in the code below. Report a defect "
            "only when you can name the file, the symbol, and the concrete input or "
            "state that produces the wrong behaviour, and say what the wrong "
            "behaviour is. Prefer few certain findings to many possible ones. "
            "**If you suspect a problem but cannot locate it in the material, omit "
            "it entirely** -- an unlocatable finding cannot be checked and reads "
            "exactly like one that can. Do not report style, formatting, or missing "
            "tests here. Note that you are seeing a selected subset of the "
            "repository, so absence of a bug is not evidence of correctness, and "
            "say so if the selection looks too partial to judge. **If you find "
            "no locatable defect, say exactly that and name how many files you "
            "read** -- a report that describes the code instead of answering is "
            "worse than one that reports nothing, because only the second can be "
            "acted on."
            " Keep the whole report under 400 words and at most eight findings, "
            "ordered by what matters most. Brevity is not a style preference "
            "here: the envelope is JSON, and an answer that runs long is cut "
            "off mid-string and parses as nothing at all -- a ninth finding "
            "costs the other eight."
        ),
        "archivable": False,
    },
    "repo_deps": {
        "data_type": "code_review",
        "label": "Dependencies",
        "describe": "Advisories from the supplied vulnerability data, plus pinning, "
                    "abandonment and licence problems visible in the manifests.",
        # The hard constraint here is not a preference. A model asked whether a
        # version is vulnerable produces fluent, plausible, wrong CVE numbers,
        # and a wrong advisory is a security claim about somebody's software.
        # The facts come from OSV in the job; this reasons over them and over
        # what the manifests plainly show.
        "prompt": (
            "You are reviewing dependencies from the manifests, lockfiles and "
            "vulnerability data below. **Report a vulnerability only if it appears "
            "in the supplied vulnerability data.** Never state, imply or guess that "
            "a package or version is affected by an advisory that is not in that "
            "data, and never invent an advisory identifier -- if the data is absent "
            "or empty, say that no vulnerability data was supplied and report "
            "nothing about vulnerabilities. Separately, report what the manifests "
            "themselves show: unpinned or wide version ranges, two major versions "
            "of one package resolved together, direct use of something declared "
            "only transitively, dependencies that appear unmaintained where the "
            "material says so, and licence terms that conflict with the project's "
            "own. Name the package and version for every finding."
            " Keep the whole report under 400 words and at most eight findings, "
            "ordered by what matters most. Brevity is not a style preference "
            "here: the envelope is JSON, and an answer that runs long is cut "
            "off mid-string and parses as nothing at all -- a ninth finding "
            "costs the other eight."
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

    # `data_type` is in the hash because it selects the output *schema*, and a
    # generator whose schema changed produces incomparable artifacts just as
    # surely as one whose prompt changed. Without it, switching a generator to a
    # different envelope leaves every earlier artifact claiming to be current --
    # and for the checkpoint pair, leaves a comparison silently diffing two
    # different shapes.
    spec = {"envelope": "core-v1", "purpose": "derive", "generator": generator,
            "data_type": GENERATORS[generator].get("data_type"),
            "prompt": GENERATORS[generator]["prompt"]}
    version = generator_version(
        purpose=EXTRACT_PURPOSE, model_id=extractor.model_id, spec=spec)
    await pool.execute(
        "INSERT INTO generators (generator_version, purpose, model_id, spec) "
        "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (generator_version) DO NOTHING",
        version, EXTRACT_PURPOSE, extractor.model_id, json.dumps(spec))
    return version


async def store_artifact(
    conn, *, org_id: str, project_id: str, kind: str, label: str,
    envelope, model_id: str | None, version: str,
    members: list[dict], offsets: list[tuple[str, int, int]],
) -> str:
    """Write one artifact and its sources. One path, and deliberately one.

    Extracted from `derive` when checkpoint comparison needed to store an
    artifact whose input is two *other artifacts* rather than a member set. The
    alternative was a second INSERT, and a second INSERT is a second place for
    the ACL rule to be got wrong -- which is the one thing here that fails
    invisibly, because an artifact with the wrong level is readable rather than
    broken.
    """
    acl = strictest([Acl(m["access_level"], _shared(m["shared_with"])) for m in members])
    artifact_id = new_id("art")
    await conn.execute(
        """
        INSERT INTO artifacts (artifact_id, org_id, project_id, kind, title,
            summary, model_id, generator_version, served_by_model,
            access_level, shared_with, owner_id, fields)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $7, $9, $10::jsonb, $11,
                $12::jsonb)
        """,
        artifact_id, org_id, project_id, kind,
        (getattr(envelope, "title", None) or label)[:200],
        getattr(envelope, "summary", None) or "",
        model_id or "unknown",
        version, acl.access_level, json.dumps(acl.shared_with),
        # Owned by whoever owns the record whose ACL this inherited. A
        # `private` artifact with no owner is readable by nobody -- the
        # predicate is `owner_id = $user`, and NULL matches none -- so it
        # would exist, be correct, and be invisible to everyone including
        # the person who asked for it.
        _owner_of(members, acl.access_level),
        # Everything the envelope carried beyond the core columns --
        # `findings` for a review, `state` and `changes` for a checkpoint,
        # and whatever a later generator adds.
        #
        # **The dict, not `json.dumps` of it.** The pool sets a jsonb codec
        # whose encoder is already `json.dumps`, so dumping first stores a jsonb
        # *string* containing JSON rather than a JSON object. It reads back as
        # `'{"findings": [...]}'`, every consumer that does `fields.findings`
        # gets undefined, and nothing errors -- the artifact is there, the
        # summary renders, and the structured half is silently text. Migration
        # 0054 repairs the rows written before this line was right.
        getattr(envelope, "fields", None) or {},
    )
    for data_id, start, end in offsets:
        await conn.execute(
            "INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            artifact_id, data_id, start, end)
    return artifact_id


async def derive(
    pool: asyncpg.Pool, principal: Principal, memory_id: str, *,
    generator: str = "summary", extractor=None, archive: bool = False,
    max_members: int = 200, dry_run: bool = False,
    only: list[str] | None = None,
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

    # **Some members are provenance, not reading material.**
    #
    # The raw code graph is a megabyte of node-link JSON stored so the graph can
    # be traversed or re-read later. Handed to an extractor it fills every
    # window with punctuation, and the reports come back as an echo of their own
    # input -- which is what happened, and looks like a model failure while
    # being a units failure. It used to be kept out of the way by living in a
    # sibling memory, which cost a second, untitled, unidentifiable entry in
    # every picker for every snapshot. Skipping it here is the same protection
    # without that cost: one memory per snapshot, holding everything about it.
    #
    # Tagged rather than typed, so a producer can mark anything this way without
    # a schema change, and so nothing outside this filter has to know.
    # `only` narrows to named members, for a generator whose subject is one
    # record rather than the container. A checkpoint describes the record that
    # created it; run over the whole memory it would describe the timeline, and
    # every checkpoint in a ten-entry timeline would come back saying roughly
    # the same thing. It filters rather than fetches so the ACL, the hierarchy
    # and the skip-tag all still apply -- naming a member you cannot see gets
    # you nothing, not a bypass.
    members = [
        m for m in await _members(pool, principal, memory_id)
        if SKIP_TAG not in (m.get("tags") or [])
        and (only is None or m["data_id"] in set(only))
    ][:max_members]
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

    # The *shape* of the answer, not only the wording of the question. A
    # generator asking for located defects gets an envelope with a `findings`
    # array; everything else gets the ordinary document envelope. Telling a
    # summariser to find bugs and leaving the schema alone produces a fluent
    # description of the material every time -- which is what this fixes.
    with span("derive", generator=generator, memory_id=memory_id):
        envelope = await extractor.extract(
            "\n\n".join(joined)[:200_000],
            data_type=spec.get("data_type") or "document_text",
            prompt=spec["prompt"])

    # The strictest ACL among the sources, which is the rule everywhere a
    # derived thing spans several records: a study guide over a private and two
    # org documents is private, or deriving becomes a way to widen visibility.
    version = await register(pool, generator, extractor)

    async with pool.acquire() as conn, conn.transaction():
        artifact_id = await store_artifact(
            conn, org_id=memory["org_id"], project_id=memory["project_id"],
            kind=generator, label=GENERATORS[generator]["label"],
            envelope=envelope, model_id=getattr(extractor, "model_id", None),
            version=version, members=members, offsets=offsets)
        acl = strictest(
            [Acl(m["access_level"], _shared(m["shared_with"])) for m in members])
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
               -- `fields` carries what the core columns cannot: `findings` for
               -- a review, `quality` for a judged page. Selecting the columns
               -- and not this returns an artifact that answered the question
               -- with the answer removed.
               a.fields,
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
