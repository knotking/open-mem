"""Checkpoint timelines: what changed since last time.

A memory is otherwise a bag of records with a lifetime, and nothing in it says
that *this* record is a later version of the same thing as *that* one. A corpus
fed the same document repeatedly -- a nightly export, a weekly status report, a
vendor feed -- accumulates copies and answers from all of them at once, and the
question people actually have about such a memory is not "what does it say" but
"what moved".

A memory *type* can be marked `checkpoints`. Every record added to a memory of
that type becomes a checkpoint, is summarised on its own, and is compared
against the checkpoint before it.

**The comparison is between two summaries, not two documents.** That is the
requested design and it has one failure mode worth naming, because it is the
kind that hides: two free-prose summaries of the same content differ in wording
on every run, so a diff of them always finds something, so it always looks like
it is working. Three things hold that off, and none of them is the prompt:

1. **The state schema is a fixed-order list of observations**, not a paragraph
   (`extraction.STATE_PROPERTIES`). Two runs over the same content fill the same
   slots, so what differs between them is content rather than phrasing.
2. **An identical checksum short-circuits the whole check.** Byte-identical
   content cannot have changed, so no model is asked. This costs nothing and
   removes the single most common false positive outright.
3. **Two states made by different generator versions are not compared.** If the
   prompt or the model moved between them, a diff reports that drift as content
   change -- confidently, and in the same shape as a real finding. The
   checkpoint is marked `incomparable` and says so.

**Capture is free and the check is not.** Adding the row is one INSERT on the
write path; the summarise-and-compare is a queue message, so a bulk import
costs one row per record immediately and the model calls arrive behind it.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from .audit import record_audit
from .auth import DATA_READ, DATA_WRITE, Principal
from .ids import new_id

log = logging.getLogger(__name__)

# The same string `events.TOPIC_FOR_EVENT` routes `checkpoint.captured` onto.
# Two spellings would mean the sweep publishes into a topic nothing subscribes
# to, which is silent and looks exactly like a sweep with nothing to do.
CHECKPOINT_TOPIC = "checkpoint"

STATE_GENERATOR = "checkpoint_state"
CHANGE_GENERATOR = "checkpoint_change"

# Checkpoints in one timeline are inherently sequential -- N needs N-1's state
# -- so this bounds a sweep rather than a batch. Without it one bulk import
# becomes a single unbounded run.
SWEEP_LIMIT = 25

# How much of a delta the event carries. The whole of it is in the artifact and
# `change_artifact_id` reaches it; what goes in the payload is only what a
# selector has to be able to filter on without a second authenticated call.
#
# Bounded here as well as in the generator -- which is asked for at most twenty
# changes -- because these are different bounds protecting different things. A
# payload that grows with the document is a write-path regression nobody
# notices until `domain_events` is the largest table in the database.
DIGEST_STATEMENTS = 5
STATEMENT_CHARS = 200

# Ascending, so `max` over it is the answer. Anything a generator returns that
# is not in this list ranks below `low` rather than raising: a change detector
# must not lose a real finding because the model spelled its severity oddly.
SIGNIFICANCE_ORDER = ("low", "medium", "high")


def digest(changes: list | None) -> dict:
    """What a subscriber can filter on without fetching the artifact.

    Every field here is a **scalar or a short bounded list**, and that is chosen
    against `alerts.OPERATORS` rather than for tidiness. `in` is `v in arg`,
    which is false for every list-valued `v`, so a list-valued payload field is
    only reachable through `contains` and its stringify-and-substring
    semantics. Scalars are what make a selector precise -- `{"added": {"op":
    "gt", "value": 0}}` and `{"highest_significance": ["high"]}` both work, and
    neither would if these were nested inside one `changes` object.

    An empty list gives counts of zero and `highest_significance` of None, which
    is the honest shape for "compared and found nothing" -- the same claim the
    outcome column makes, and not the same as a key that never arrived.
    """
    rows = [c for c in (changes or []) if isinstance(c, dict)]
    kinds = [str(c.get("kind") or "") for c in rows]
    ranked = [SIGNIFICANCE_ORDER.index(s) for s in
              (str(c.get("significance") or "") for c in rows)
              if s in SIGNIFICANCE_ORDER]
    return {
        "changes": len(rows),
        "added": kinds.count("added"),
        "removed": kinds.count("removed"),
        "changed": kinds.count("changed"),
        "highest_significance":
            SIGNIFICANCE_ORDER[max(ranked)] if ranked else None,
        # Truncated per statement rather than dropped, so a long one still
        # matches a `contains` on a word near its start. The generator is told
        # to keep these to one sentence; this is the guard for when it does not.
        "statements": [str(c.get("statement") or "")[:STATEMENT_CHARS]
                       for c in rows[:DIGEST_STATEMENTS]],
    }


class CheckpointError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def tracks_change(conn, project_id: str, type_name: str) -> bool:
    """Whether this memory type is a checkpoint timeline.

    Read per write, which is why it is one indexed lookup and nothing more. A
    type with no row is not a timeline: `checkpoints` is opt-in, and a default
    that quietly starts spending on every memory in a project is the wrong
    default however useful it looks on one.
    """
    return bool(await conn.fetchval(
        "SELECT checkpoints FROM memory_types WHERE project_id = $1 AND name = $2",
        project_id, type_name,
    ))


async def capture(conn, *, org_id: str, project_id: str, memory_id: str,
                  data_id: str) -> str | None:
    """Record that this member is a checkpoint. One INSERT, no model call.

    Returns the checkpoint id, or None if this memory already has a checkpoint
    for this record -- re-adding a record already in a memory is not a new
    checkpoint, and the unique key is what says so rather than a check here that
    could be forgotten.

    `seq` and `previous_id` are resolved in the caller's transaction, so two
    concurrent writes cannot both claim the same position: the unique key on
    `(memory_id, seq)` turns that race into a constraint violation rather than
    a timeline with two entries numbered four.
    """
    previous = await conn.fetchrow(
        "SELECT checkpoint_id, seq FROM memory_checkpoints "
        "WHERE memory_id = $1 ORDER BY seq DESC LIMIT 1",
        memory_id,
    )
    # Captured now rather than joined at compare time: a re-parse writes a new
    # revision, so the record's checksum can move underneath a checkpoint, and
    # the comparison has to be against what was actually seen.
    #
    # `data_items.checksum` is only set when there were bytes -- an inline text
    # write leaves it NULL -- so for text it is derived here from the text
    # itself. Without this the short-circuit is dead code for the single most
    # common kind of record, and dead in the quietest way: every checkpoint
    # would simply go on to pay for a comparison, and nothing would look wrong.
    #
    # **NULL means "not known", never "equal".** A record with no text yet
    # produces NULL rather than the hash of an empty string, or every unparsed
    # PDF in a timeline would be identical to every other one and the whole
    # feed would settle as unchanged.
    checksum = await conn.fetchval(
        """
        SELECT CASE
            WHEN checksum IS NOT NULL THEN checksum
            WHEN coalesce(content_text, '') <> ''
                THEN encode(sha256(convert_to(content_text, 'UTF8')), 'hex')
        END
        FROM data_items WHERE data_id = $1
        """,
        data_id)

    checkpoint_id = new_id("cpt")
    inserted = await conn.fetchval(
        """
        INSERT INTO memory_checkpoints (checkpoint_id, org_id, project_id,
            memory_id, data_id, seq, checksum, previous_id, outcome)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (memory_id, data_id) DO NOTHING
        RETURNING checkpoint_id
        """,
        checkpoint_id, org_id, project_id, memory_id, data_id,
        (previous["seq"] + 1) if previous else 1,
        checksum,
        previous["checkpoint_id"] if previous else None,
        # The first entry in a timeline has nothing to compare against. That is
        # a real state, not a missing one, and recording it here means the
        # worker never has to decide whether an absent comparison is a first
        # entry or a check that failed to run.
        None if previous else "first",
    )
    return inserted


async def _states(conn, checkpoint_id: str) -> dict | None:
    return await conn.fetchrow(
        """
        SELECT c.checkpoint_id, c.memory_id, c.data_id, c.seq, c.checksum,
               c.previous_id, c.state_artifact_id, c.org_id, c.project_id,
               p.data_id AS previous_data_id, p.checksum AS previous_checksum,
               p.state_artifact_id AS previous_state_id,
               -- Carried so the transition can name it. `checkpoint.captured`
               -- has always had it; `checkpoint.changed` had not, and the
               -- selector field `alerts.SURFACES` declares for this surface is
               -- exactly the one nothing populated.
               m.type AS memory_type
        FROM memory_checkpoints c
        LEFT JOIN memory_checkpoints p ON p.checkpoint_id = c.previous_id
        JOIN memories m ON m.memory_id = c.memory_id
        WHERE c.checkpoint_id = $1
        """,
        checkpoint_id,
    )


async def _finish(pool, checkpoint_id: str, *, status: str,
                  outcome: str | None = None, reason: str | None = None,
                  state_artifact_id: str | None = None,
                  change_artifact_id: str | None = None) -> None:
    await pool.execute(
        """
        UPDATE memory_checkpoints
        SET status = $2, outcome = coalesce($3, outcome), reason = $4,
            state_artifact_id = coalesce($5, state_artifact_id),
            change_artifact_id = coalesce($6, change_artifact_id),
            updated_at = now()
        WHERE checkpoint_id = $1
        """,
        checkpoint_id, status, outcome, reason,
        state_artifact_id, change_artifact_id,
    )


async def run_check(pool: asyncpg.Pool, principal: Principal, extractor,
                    checkpoint_id: str) -> dict:
    """Summarise this checkpoint's record, then compare it with the one before.

    Every exit writes a terminal `status`, including the ones that do no work.
    A checkpoint left `pending` reads as one still running, which is the same
    lie a snapshot left pending told before `repos.report_result` existed.
    """
    from .derive import GENERATORS, derive, register, store_artifact
    from .extraction import Envelope

    row = await _states(pool, checkpoint_id)
    if row is None:
        raise CheckpointError("no such checkpoint", status=404)
    if row["org_id"] != principal.org_id:
        raise CheckpointError("no such checkpoint", status=404)

    await pool.execute(
        "UPDATE memory_checkpoints SET status = 'running', updated_at = now() "
        "WHERE checkpoint_id = $1", checkpoint_id)

    # ------------------------------------------------- the free answer first
    #
    # Byte-identical content cannot have changed, and this is checked *before*
    # anything is described rather than after. Deriving a description of a
    # record identical to the last one is a model call whose answer is already
    # sitting in the database.
    #
    # The predecessor's description is reused rather than regenerated, which is
    # not a shortcut but the more correct answer twice over: identical content
    # genuinely has an identical state, and sharing the artifact guarantees the
    # *next* comparison finds a predecessor at the same generator version
    # instead of tripping the drift guard on a version that moved in between.
    if (row["previous_id"] is not None
            and row["checksum"]
            and row["checksum"] == row["previous_checksum"]
            and row["previous_state_id"] is not None):
        await _finish(pool, checkpoint_id, status="complete", outcome="unchanged",
                      state_artifact_id=row["previous_state_id"],
                      reason="identical to the previous checkpoint, byte for byte")
        return {"checkpoint_id": checkpoint_id, "status": "complete",
                "outcome": "unchanged", "state_artifact_id": row["previous_state_id"],
                "model_calls": 0}

    # ------------------------------------------------- nothing to describe
    #
    # A record whose bytes were never read has no text, and describing it
    # anyway produces a confident description of an empty string -- which then
    # compares cleanly against the next one and reports "nothing changed" about
    # a document nobody has read. That is the exact shape of the failure this
    # codebase keeps meeting: the pipeline succeeds, the state says so, and the
    # part that mattered silently never happened.
    readable = await pool.fetchval(
        "SELECT coalesce(indexable_text, '') <> '' FROM data_items "
        "WHERE data_id = $1 AND deleted_at IS NULL",
        row["data_id"])
    if not readable:
        reason = ("the record has no readable text yet -- its bytes were never "
                  "parsed, so there is nothing to describe or compare")
        await _finish(pool, checkpoint_id, status="failed", reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "failed",
                "outcome": None, "reason": reason}

    # ---------------------------------------------------------------- state
    try:
        made = await derive(
            pool, principal, row["memory_id"], generator=STATE_GENERATOR,
            extractor=extractor, only=[row["data_id"]])
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        await _finish(pool, checkpoint_id, status="failed", reason=str(exc)[:500])
        raise

    state_id = made.get("artifact_id")
    if state_id is None:
        # `derive` returns without an artifact when nothing readable was in
        # scope -- an unparsed PDF, a record the caller cannot see. Not a
        # failure of the check; a failure to have anything to check.
        reason = made.get("note") or "the record had no readable text to describe"
        await _finish(pool, checkpoint_id, status="failed", reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "failed", "reason": reason}

    if row["previous_id"] is None:
        await _finish(pool, checkpoint_id, status="complete", outcome="first",
                      state_artifact_id=state_id)
        return {"checkpoint_id": checkpoint_id, "status": "complete",
                "outcome": "first", "state_artifact_id": state_id}

    # ------------------------------------------------------ the drift guards
    #
    # The checksum case is settled above. What is left is content that really
    # differs, or a predecessor this cannot be compared against.
    previous = await pool.fetchrow(
        "SELECT artifact_id, summary, fields, generator_version, access_level, "
        "       shared_with, owner_id "
        "FROM artifacts WHERE artifact_id = $1 AND deleted_at IS NULL",
        row["previous_state_id"])
    current = await pool.fetchrow(
        "SELECT artifact_id, summary, fields, generator_version, access_level, "
        "       shared_with, owner_id "
        "FROM artifacts WHERE artifact_id = $1", state_id)

    if previous is None:
        reason = ("the previous checkpoint has no state to compare against -- "
                  "it has not been checked yet, or its description was erased")
        await _finish(pool, checkpoint_id, status="complete", outcome="incomparable",
                      state_artifact_id=state_id, reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "complete",
                "outcome": "incomparable", "reason": reason}

    # Two states written by different generator versions differ because the
    # prompt or the model moved, and a diff of them reports that drift as
    # content change -- in exactly the shape a real finding has. Refusing is the
    # honest answer; `recheck` on the earlier one is the way forward.
    if previous["generator_version"] != current["generator_version"]:
        reason = ("the previous checkpoint was described by a different version "
                  "of this generator, so a comparison would report the change of "
                  "prompt as a change of content")
        await _finish(pool, checkpoint_id, status="complete", outcome="incomparable",
                      state_artifact_id=state_id, reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "complete",
                "outcome": "incomparable", "reason": reason}

    # ---------------------------------------------------------- the compare
    text = (
        "EARLIER:\n" + _render(previous) + "\n\nLATER:\n" + _render(current)
    )
    spec = GENERATORS[CHANGE_GENERATOR]
    try:
        envelope = await extractor.extract(
            text[:200_000], data_type=spec["data_type"], prompt=spec["prompt"])
    except Exception as exc:  # noqa: BLE001
        await _finish(pool, checkpoint_id, status="failed",
                      state_artifact_id=state_id, reason=str(exc)[:500])
        raise

    changes = (getattr(envelope, "fields", None) or {}).get("changes")
    version = await register(pool, CHANGE_GENERATOR, extractor)

    # The comparison's sources are the two RECORDS, not the two descriptions:
    # that is what makes erasing either one cascade correctly onto the change
    # record built from it, and it is what a citation has to be able to open.
    members = [
        {"data_id": row["data_id"], "access_level": current["access_level"],
         "shared_with": current["shared_with"], "owner_id": current["owner_id"]},
        {"data_id": row["previous_data_id"], "access_level": previous["access_level"],
         "shared_with": previous["shared_with"], "owner_id": previous["owner_id"]},
    ]
    async with pool.acquire() as conn, conn.transaction():
        change_id = await store_artifact(
            conn, org_id=row["org_id"], project_id=row["project_id"],
            kind=CHANGE_GENERATOR, label=spec["label"], envelope=envelope,
            model_id=getattr(extractor, "model_id", None), version=version,
            members=members,
            offsets=[(m["data_id"], 0, 0) for m in members if m["data_id"]])
        await record_audit(
            conn, principal, action="checkpoint.checked",
            project_id=row["project_id"], target_type="memory",
            target_id=row["memory_id"],
            detail={"checkpoint_id": checkpoint_id, "seq": row["seq"],
                    "changes": len(changes or [])},
        )

    # An empty list is the answer for a record that did not move, and it is
    # stored as one. "Compared and found nothing" and "never compared" are
    # different claims -- the whole question this feature exists to answer.
    outcome = "changed" if changes else "unchanged"
    await _finish(pool, checkpoint_id, status="complete", outcome=outcome,
                  state_artifact_id=state_id, change_artifact_id=change_id)

    if outcome == "changed":
        from .alerts import emit_transition

        async with pool.acquire() as conn, conn.transaction():
            await emit_transition(
                conn, "checkpoint.changed", org_id=row["org_id"],
                project_id=row["project_id"], data_id=row["data_id"],
                payload={"memory_id": row["memory_id"],
                         "memory_type": row["memory_type"],
                         "checkpoint_id": checkpoint_id, "seq": row["seq"],
                         # The whole delta, for a subscriber that wants more
                         # than the digest. A push delivery carries the event
                         # payload and nothing else, so without this the only
                         # way to find out *what* moved is a second call.
                         "change_artifact_id": change_id,
                         **digest(changes)},
            )

    return {"checkpoint_id": checkpoint_id, "status": "complete",
            "outcome": outcome, "state_artifact_id": state_id,
            "change_artifact_id": change_id, "changes": len(changes or [])}


def _render(artifact) -> str:
    """A state as the comparison reads it: the observations, one per line.

    The list rather than the summary, and the summary only when the list is
    missing. Comparing the prose is the thing this design is arranged to avoid,
    so it is the fallback rather than the input.
    """
    fields = artifact["fields"]
    if isinstance(fields, str):
        fields = json.loads(fields)
    observations = (fields or {}).get("state")
    if observations:
        return "\n".join(f"- {line}" for line in observations)
    return artifact["summary"] or ""


async def timeline(pool: asyncpg.Pool, principal: Principal, memory_id: str,
                   *, limit: int = 100) -> list[dict]:
    """One memory's checkpoints, newest first, each with what changed.

    ACL-filtered on the change artifact rather than the checkpoint: an artifact
    takes the strictest level among its sources, so a timeline can legitimately
    show fewer change records than it has checkpoints.
    """
    from .acl import visibility_params, visibility_sql

    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT c.checkpoint_id, c.seq, c.data_id, c.status, c.outcome, c.reason,
               c.created_at, c.updated_at,
               d.external_id,
               a.artifact_id AS change_artifact_id, a.fields AS change_fields,
               a.summary AS change_summary
        FROM memory_checkpoints c
        JOIN data_items d ON d.data_id = c.data_id
        LEFT JOIN artifacts a
               ON a.artifact_id = c.change_artifact_id
              AND {visibility_sql("a", 1, 2, 3)}
        WHERE c.memory_id = $4 AND c.org_id = $1
        ORDER BY c.seq DESC
        LIMIT $5
        """,
        org_id, user_id, principals, memory_id, limit,
    )
    # Normalised on the way out. Rows written before migration 0054 hold a jsonb
    # *string* rather than an object, and a console doing `change_fields.changes`
    # on one gets undefined and renders an empty timeline entry -- which for a
    # change detector reads as "nothing moved".
    entries = []
    for row in rows:
        entry = dict(row)
        if isinstance(entry.get("change_fields"), str):
            try:
                entry["change_fields"] = json.loads(entry["change_fields"])
            except ValueError:
                entry["change_fields"] = None
        entries.append(entry)
    return entries


async def pending(pool: asyncpg.Pool, *, grace_seconds: int = 0,
                  limit: int = SWEEP_LIMIT) -> list[dict]:
    """Checkpoints the worker has not finished, oldest first.

    Oldest first and in `seq` order per memory, because a comparison needs its
    predecessor's description to exist: running a timeline backwards produces a
    run of `incomparable` and looks like the guard misfiring.
    """
    rows = await pool.fetch(
        """
        SELECT checkpoint_id, memory_id, seq FROM memory_checkpoints
        WHERE status IN ('pending', 'running')
          AND created_at < now() - make_interval(secs => $1)
        ORDER BY memory_id, seq
        LIMIT $2
        """,
        grace_seconds, limit,
    )
    return [dict(r) for r in rows]


async def recheck(pool: asyncpg.Pool, principal: Principal, queue,
                  checkpoint_id: str) -> dict:
    """Run one checkpoint again.

    The way out of `incomparable`, and the way to pick up an edited prompt: this
    re-derives the description at the current generator version, which is what
    makes the pair comparable again.
    """
    principal.require(DATA_WRITE)
    row = await pool.fetchrow(
        "SELECT checkpoint_id, org_id FROM memory_checkpoints WHERE checkpoint_id = $1",
        checkpoint_id)
    if row is None or row["org_id"] != principal.org_id:
        raise CheckpointError("no such checkpoint", status=404)
    await pool.execute(
        "UPDATE memory_checkpoints SET status = 'pending', outcome = NULL, "
        "reason = NULL, updated_at = now() WHERE checkpoint_id = $1", checkpoint_id)
    await queue.publish(
        CHECKPOINT_TOPIC, {"payload": {"checkpoint_id": checkpoint_id}})
    return {"checkpoint_id": checkpoint_id, "status": "pending"}
