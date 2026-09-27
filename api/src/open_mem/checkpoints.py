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


def _stopped_because(exc: BaseException) -> str:
    """`deferred` when the provider said "not now", `failed` when it said no.

    The same question `workers._is_capacity` already answers for the queue, and
    deliberately the same function rather than a second opinion: the queue
    retries a capacity error and drops everything else, so a row that disagreed
    with it would say `failed` about work that is coming back, or `deferred`
    about work that is not. Both read as the system lying about its own state.
    """
    from .workers import _is_capacity

    return "deferred" if _is_capacity(exc) else "failed"


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


async def _finish(pool, checkpoint_id: str, *, row, status: str,
                  outcome: str | None = None, reason: str | None = None,
                  state_artifact_id: str | None = None,
                  change_artifact_id: str | None = None,
                  changes: list | None = None) -> None:
    """Settle the row and announce it, in one transaction.

    **The announcement is here rather than at each of the nine exits, because
    here is the only place all nine agree on.** A surface whose entire purpose
    is that "checked and found nothing" is distinguishable from "never checked"
    cannot be emitted from some exits and forgotten at others -- the forgotten
    ones would be precisely the quiet failures, since those are the paths nobody
    watches and the reason the surface is being added at all.

    One transaction with the status it describes. Two would allow a process to
    die in between and leave a checkpoint that is complete in the table and
    never happened in the stream, which is the lost-work shape `domain_events`
    exists to refuse.

    What `RETURNING` gives is what the event reports, never the arguments passed
    in: `outcome` coalesces, so a failed check on a first checkpoint keeps
    `first`, and an event announcing the argument would be announcing something
    that was not stored.
    """
    from .alerts import emit_transition

    async with pool.acquire() as conn, conn.transaction():
        settled = await conn.fetchrow(
            """
            UPDATE memory_checkpoints
            SET status = $2, outcome = coalesce($3, outcome), reason = $4,
                state_artifact_id = coalesce($5, state_artifact_id),
                change_artifact_id = coalesce($6, change_artifact_id),
                updated_at = now()
            WHERE checkpoint_id = $1
            RETURNING status, outcome, reason, change_artifact_id
            """,
            checkpoint_id, status, outcome, reason,
            state_artifact_id, change_artifact_id,
        )
        # Erased underneath the check -- a record deleted while its comparison
        # was in flight takes its checkpoint with it. Nothing to settle and
        # nothing to announce; announcing it would put a memory_id in the stream
        # that no longer resolves.
        if settled is None:
            return

        payload = {
            "memory_id": row["memory_id"], "memory_type": row["memory_type"],
            "checkpoint_id": checkpoint_id, "seq": row["seq"],
            "change_artifact_id": settled["change_artifact_id"],
            **digest(changes),
        }

        # Every exit, including the ones that did no work. This is the event a
        # downstream sync advances a watermark on, and for that "we looked and
        # nothing moved" has to be a message rather than a silence -- the row
        # has always drawn that distinction and the stream did not.
        await emit_transition(
            conn, "checkpoint.checked", org_id=row["org_id"],
            project_id=row["project_id"], data_id=row["data_id"],
            payload={**payload, "status": settled["status"],
                     "outcome": settled["outcome"], "reason": settled["reason"]},
        )

        # And, separately, real movement. Kept as its own surface rather than
        # folded into the one above: a subscriber who wants to be told when the
        # feed moves should not have to write a selector to avoid being told
        # when it did not, and every alert already written against this surface
        # keeps meaning what it meant.
        if settled["outcome"] == "changed":
            await emit_transition(
                conn, "checkpoint.changed", org_id=row["org_id"],
                project_id=row["project_id"], data_id=row["data_id"],
                payload=payload,
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
        await _finish(pool, checkpoint_id, row=row, status="complete", outcome="unchanged",
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
        await _finish(pool, checkpoint_id, row=row, status="failed", reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "failed",
                "outcome": None, "reason": reason}

    # ---------------------------------------------------------------- state
    try:
        made = await derive(
            pool, principal, row["memory_id"], generator=STATE_GENERATOR,
            extractor=extractor, only=[row["data_id"]])
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        await _finish(pool, checkpoint_id, row=row, status=_stopped_because(exc),
                      reason=str(exc)[:500])
        raise

    state_id = made.get("artifact_id")
    if state_id is None:
        # `derive` returns without an artifact when nothing readable was in
        # scope -- an unparsed PDF, a record the caller cannot see. Not a
        # failure of the check; a failure to have anything to check.
        reason = made.get("note") or "the record had no readable text to describe"
        await _finish(pool, checkpoint_id, row=row, status="failed", reason=reason)
        return {"checkpoint_id": checkpoint_id, "status": "failed", "reason": reason}

    if row["previous_id"] is None:
        await _finish(pool, checkpoint_id, row=row, status="complete", outcome="first",
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
        await _finish(pool, checkpoint_id, row=row, status="complete", outcome="incomparable",
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
        await _finish(pool, checkpoint_id, row=row, status="complete", outcome="incomparable",
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
        await _finish(pool, checkpoint_id, row=row, status=_stopped_because(exc),
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
    await _finish(pool, checkpoint_id, row=row, status="complete", outcome=outcome,
                  state_artifact_id=state_id, change_artifact_id=change_id,
                  changes=changes)

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
        -- `deferred` too, and it is the reason this sweep matters rather than
        -- merely tidying: the queue message that would have retried a
        -- rate-limited check was already consumed, so without the sweep the
        -- work waits for a nudge that is never coming.
        WHERE status IN ('pending', 'running', 'deferred')
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


# ------------------------------------------------- a range of the timeline
#
# The comparison a checkpoint does is against the one immediately before it, and
# that is the only comparison the timeline has ever been able to answer. The
# question people actually ask a feed is *"what has changed since Monday"* or
# *"since the version I last synced"* -- a range, not an adjacency.
#
# It is derivable from the timeline endpoint by a caller willing to walk it,
# which is precisely the argument for building it here once rather than watching
# three callers each write a different half-correct version of the walk.

# The widest span this will compose. Refused past it rather than truncated: a
# range that quietly returns its most recent 500 entries while claiming to be
# the range that was asked for is the silent-truncation failure this codebase
# has met before, and for a change detector it reads as "nothing else moved".
RANGE_LIMIT = 500

# Why a checkpoint inside a range contributed no delta. Closed, for the reason
# every vocabulary here is closed: an open one degrades into unqueryable free
# text, and "which of my ranges have holes, and of what kind" stops being
# answerable.
#
# `not_visible` is in the list because the alternative is worse. An artifact
# takes the strictest level among its sources, so a reader can legitimately be
# unable to see one change in a range they can otherwise read -- and reporting
# that as no-change would be the same lie as reporting a failed check as one.
# It leaks nothing `timeline` does not already: that function returns every
# checkpoint row and its outcome to any reader in the org, and ACL-filters only
# the artifact hanging off it.
GAP_REASONS = ("not_checked", "failed", "incomparable", "not_visible")


def _point(value) -> tuple[str, object]:
    """How a caller named a position: by sequence, by checkpoint, or by time.

    Three spellings because the three questions are genuinely different. A
    consumer resuming a sync has the `checkpoint_id` it stopped at; a person
    asks for "since Monday"; a script counting entries has a `seq`. Guessing
    between them from the string is safe only because the three shapes cannot
    collide -- a prefixed ULID, a bare integer, and a timestamp.
    """
    text = str(value).strip()
    if text.startswith("cpt_"):
        return "checkpoint_id", text
    if text.lstrip("-").isdigit():
        return "seq", int(text)
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckpointError(
            f"cannot read {value!r} as a position on the timeline; use a "
            "checkpoint id, a sequence number, or an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return "at", parsed


async def _locate(pool, memory_id: str, org_id: str, value) -> dict | None:
    """Which checkpoint a position lands on. One indexed lookup, not a scan.

    Resolved with a targeted query rather than by walking the timeline: the span
    is capped at `RANGE_LIMIT`, but the *timeline* is not, and a vendor feed
    running for two years has thousands of entries that a range over last week
    has no business reading.

    A timestamp resolves to the last checkpoint **at or before** it, which is the
    only reading that makes "since Monday" mean what it says when nothing was
    captured on Monday. A `seq` or an id that names nothing is an error rather
    than a silent slide to the nearest -- a sync resuming from a checkpoint that
    has been erased must hear about it, not quietly re-report a month.
    """
    kind, wanted = _point(value)
    columns = "SELECT checkpoint_id, seq, created_at FROM memory_checkpoints "
    if kind == "at":
        # Before the timeline began is a real position and not an error: it
        # means "from the start", which is what someone asking for a window
        # wider than the feed's history means.
        row = await pool.fetchrow(
            columns + "WHERE memory_id = $1 AND org_id = $2 AND created_at <= $3 "
            "ORDER BY seq DESC LIMIT 1", memory_id, org_id, wanted)
        return dict(row) if row else None
    row = await pool.fetchrow(
        columns + f"WHERE memory_id = $1 AND org_id = $2 AND {kind} = $3",
        memory_id, org_id, wanted)
    if row is None:
        raise CheckpointError(
            f"this timeline has no checkpoint with {kind} {wanted!r}", status=404)
    return dict(row)


def _empty(memory_id: str, note: str, *, start=None, end=None,
           basis: str = "composed") -> dict:
    """A range with nothing in it, still shaped like a range.

    Every empty answer here carries a note saying *which* empty it is. Nothing
    captured yet, a memory that will never have a timeline, and nothing new since
    you last asked are three different facts, and one blank response for all
    three is what makes a screen unreadable and a sync unable to tell whether it
    is working.

    `basis` is carried even here. A caller that asked for the net reading and
    received `composed` would be right to conclude it got the other answer --
    the field is the contract, and an empty response is not exempt from it.
    """
    return {"memory_id": memory_id, "basis": basis, "from": start, "to": end,
            "checkpoints": 0, "changes": [], "gaps": [],
            "counts": {"added": 0, "removed": 0, "changed": 0}, "note": note}


async def _no_timeline(pool, memory_id: str, org_id: str, basis: str) -> dict:
    """The answer for a memory with no checkpoints on it, whichever basis asked.

    Shared by both readings so the two empty states stay one decision. A memory
    of an ordinary type has no timeline and never will; one of a timeline type
    simply has nothing on it yet. Collapsing those into a single blank answer is
    the thing that makes a screen unreadable.
    """
    tracked = await pool.fetchval(
        """
        SELECT t.checkpoints FROM memories m
        JOIN memory_types t ON t.project_id = m.project_id AND t.name = m.type
        WHERE m.memory_id = $1 AND m.org_id = $2
        """,
        memory_id, org_id)
    if tracked is None:
        raise CheckpointError("no such memory", status=404)
    return _empty(memory_id, basis=basis, note=(
        "this memory is a checkpoint timeline with nothing on it yet" if tracked
        else "this memory is not a checkpoint timeline, so it has no changes "
             "to report"))


def _gap(row, why: str) -> dict:
    """A checkpoint in the range that contributed no delta, and why.

    Reported rather than skipped. A range with a hole in it that presents as
    complete is the same failure as a check that failed reading as "nothing
    changed" -- and here it is worse, because the reader asked a question about
    a span and got an answer about part of one.
    """
    return {"seq": row["seq"], "checkpoint_id": row["checkpoint_id"],
            "external_id": row["external_id"], "why": why,
            "detail": row["reason"]}


async def changes_between(pool: asyncpg.Pool, principal: Principal, memory_id: str,
                          *, since=None, until=None) -> dict:
    """What moved across a span of one memory's timeline.

    **Composed from the deltas already stored, so it costs nothing and every
    claim in it is backed by an artifact a citation can open.** That has one
    consequence worth saying out loud rather than leaving to be discovered:
    composing adjacent deltas reports **churn**, not net. A value that went
    green, then red, then green appears here as two changes, because two changes
    is what happened. The net reading is a different question with a different
    answer, and it costs a model call -- which is why `basis` is in the response
    and is never defaulted silently. A consumer that does not know which of the
    two it received cannot reconcile it with the other.
    """
    from .acl import visibility_params, visibility_sql

    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)

    head = await pool.fetchrow(
        "SELECT checkpoint_id, seq, created_at FROM memory_checkpoints "
        "WHERE memory_id = $1 AND org_id = $2 ORDER BY seq DESC LIMIT 1",
        memory_id, org_id)
    if head is None:
        return await _no_timeline(pool, memory_id, org_id, "composed")

    start = await _locate(pool, memory_id, org_id, since) if since else None
    end = await _locate(pool, memory_id, org_id, until) if until else dict(head)
    if end is None:
        # `until` landed before the first checkpoint: the window closes before
        # the timeline opens, so nothing is in it.
        return _empty(memory_id, "the window closes before this timeline begins")

    # Exclusive of `from`, inclusive of `to`. A range is the deltas *since* a
    # point, and the delta stored on the starting checkpoint describes how it
    # differed from the one before it -- which is outside the window and would
    # be double-counted by anyone chaining two ranges together.
    low = start["seq"] if start else 0

    # The ordinary answer for a poller, and it gets its own sentence. A consumer
    # asking "what has changed since the checkpoint I last saw" lands here every
    # time nothing new has arrived, and telling it the window was malformed
    # would be both wrong and alarming.
    if end["seq"] == low:
        return _empty(memory_id, "nothing has been captured on this timeline "
                      "since that point", start=start, end=end)

    # An inverted window is a caller mistake and is refused. Returning an empty
    # range for it would report "nothing changed" about a span that was never
    # examined -- the exact lie every other guard in this file exists to prevent,
    # arriving through the one door that looks like a valid answer.
    if end["seq"] < low:
        raise CheckpointError(
            f"`to` is at position {end['seq']} and `from` at {low}: the window "
            "ends before it starts, and an empty answer for it would read as "
            "nothing having changed")

    if end["seq"] - low > RANGE_LIMIT:
        raise CheckpointError(
            f"that span covers {end['seq'] - low} checkpoints and the limit is "
            f"{RANGE_LIMIT}; ask for a narrower window rather than receiving a "
            "truncated one")

    rows = await pool.fetch(
        f"""
        SELECT c.checkpoint_id, c.seq, c.data_id, c.status, c.outcome, c.reason,
               c.created_at, c.change_artifact_id,
               d.external_id,
               a.artifact_id AS visible_artifact_id, a.fields AS change_fields
        FROM memory_checkpoints c
        JOIN data_items d ON d.data_id = c.data_id
        LEFT JOIN artifacts a
               ON a.artifact_id = c.change_artifact_id
              AND {visibility_sql("a", 1, 2, 3)}
        WHERE c.memory_id = $4 AND c.org_id = $1
          AND c.seq > $5 AND c.seq <= $6
        ORDER BY c.seq
        """,
        org_id, user_id, principals, memory_id, low, end["seq"])

    changes: list[dict] = []
    gaps: list[dict] = []
    for row in rows:
        # `deferred` belongs with the not-yet-checked, not with the failures:
        # the sweep is going to pick it up, so a range asked again in a minute
        # will have it. Calling it `failed` would send somebody looking for a
        # defect that is a rate limit.
        if row["status"] in ("pending", "running", "deferred"):
            gaps.append(_gap(row, "not_checked"))
            continue
        if row["status"] == "failed":
            gaps.append(_gap(row, "failed"))
            continue
        if row["outcome"] == "incomparable":
            gaps.append(_gap(row, "incomparable"))
            continue
        if row["change_artifact_id"] and row["visible_artifact_id"] is None:
            gaps.append(_gap(row, "not_visible"))
            continue
        # `first` and `unchanged` reach here and contribute nothing, which is
        # correct and is not a gap: the check ran and there was nothing to
        # report. That is the distinction the outcome column exists to draw and
        # the one this function must not blur.
        fields = row["change_fields"]
        if isinstance(fields, str):
            # Rows written before migration 0054 hold a jsonb *string* rather
            # than an object; `timeline` normalises the same way.
            try:
                fields = json.loads(fields)
            except ValueError:
                fields = None
        for change in ((fields or {}).get("changes") or []):
            if not isinstance(change, dict):
                continue
            # Provenance on every row. A composed range is an assembly of other
            # people's answers, and each one has to say which checkpoint it came
            # from or none of it can be checked against the record behind it.
            changes.append({
                "seq": row["seq"], "checkpoint_id": row["checkpoint_id"],
                "data_id": row["data_id"], "external_id": row["external_id"],
                "at": row["created_at"], **change,
            })

    kinds = [c.get("kind") for c in changes]
    return {
        "memory_id": memory_id,
        # Required, never inferred. Phase 4 adds `net`, which answers a
        # different question about the same two points, and a consumer holding
        # one of them without knowing which cannot reconcile it with the other.
        "basis": "composed",
        "from": start, "to": end,
        "checkpoints": len(rows),
        "changes": changes,
        "counts": {"added": kinds.count("added"), "removed": kinds.count("removed"),
                   "changed": kinds.count("changed")},
        "gaps": gaps,
    }


# --------------------------------------------------------- the net answer
#
# `changes_between` composes the stored steps, which is free and reports churn.
# This is the other reading: what is different between the two ends, net of
# everything in between. Green, red, green composes to two changes and nets to
# none, and both are correct answers to different questions -- which is why
# `basis` is in every response and is never inferred.


async def _state_of(pool, checkpoint_id: str):
    """The description a checkpoint settled on, with its fingerprint.

    Read through the checkpoint rather than by artifact id, because the two
    things a net comparison has to know -- was this checked at all, and by which
    generator -- live on different rows.
    """
    return await pool.fetchrow(
        """
        SELECT c.checkpoint_id, c.seq, c.data_id, c.status, c.outcome, c.reason,
               d.external_id,
               a.artifact_id, a.summary, a.fields, a.generator_version,
               a.access_level, a.shared_with, a.owner_id
        FROM memory_checkpoints c
        JOIN data_items d ON d.data_id = c.data_id
        LEFT JOIN artifacts a
               ON a.artifact_id = c.state_artifact_id AND a.deleted_at IS NULL
        WHERE c.checkpoint_id = $1
        """,
        checkpoint_id)


async def net_between(pool: asyncpg.Pool, principal: Principal, extractor,
                      memory_id: str, *, since=None, until=None) -> dict:
    """What is different between two points, net of everything in between.

    One model call over the two descriptions -- never the two records, for the
    reason `derive.GENERATORS` gives: handing a generator its raw source is what
    made every repository report an echo of its own input.

    **Paid for once.** A polling consumer asks for the same range repeatedly, so
    the answer is stored in `memory_change_ranges` and keyed on the generator
    version as well as the two ends. A prompt that moved misses the cache rather
    than being answered from it.
    """
    from .derive import GENERATORS, register, store_artifact

    principal.require(DATA_READ)
    org_id = principal.org_id

    head = await pool.fetchrow(
        "SELECT checkpoint_id, seq, created_at, project_id FROM memory_checkpoints "
        "WHERE memory_id = $1 AND org_id = $2 ORDER BY seq DESC LIMIT 1",
        memory_id, org_id)
    if head is None:
        return await _no_timeline(pool, memory_id, org_id, "net")

    start = await _locate(pool, memory_id, org_id, since) if since else None
    end = await _locate(pool, memory_id, org_id, until) if until else dict(head)

    # A net comparison is between two states, so it needs two. "From the
    # beginning" has no description to compare against -- the honest answer is
    # to say so rather than quietly anchoring on the first checkpoint, which
    # would silently answer a question nobody asked.
    if start is None:
        raise CheckpointError(
            "a net comparison needs a starting point: give `from` as a "
            "checkpoint id, a sequence number, or a timestamp inside this "
            "timeline. Without one, ask for the composed range instead")
    if end is None or end["seq"] <= start["seq"]:
        raise CheckpointError(
            "`to` is at or before `from`, so there is nothing between them to "
            "compare")

    earlier = await _state_of(pool, start["checkpoint_id"])
    later = await _state_of(pool, end["checkpoint_id"])

    # Neither end usable is not an error and not "nothing changed". It is the
    # same `gaps` vocabulary the composed range uses, because a caller handling
    # one should not need a second shape to handle the other.
    holes = [
        _gap(row, "not_checked" if row["status"] in ("pending", "running")
             else "failed" if row["status"] == "failed" else "incomparable")
        for row in (earlier, later) if row["artifact_id"] is None
    ]
    if holes:
        return _range_answer(memory_id, start, end, [], gaps=holes,
                             note="one end of this window has no description to "
                                  "compare, so there is no net answer for it")

    # The same drift guard `run_check` applies to adjacent pairs, for the same
    # reason and with more force: the further apart two checkpoints are, the
    # more likely a prompt or a model moved between them, and a diff of two
    # differently-generated descriptions reports that drift as content change --
    # confidently, and in the shape a real finding has.
    if earlier["generator_version"] != later["generator_version"]:
        reason = ("the two ends were described by different versions of this "
                  "generator, so a comparison would report the change of prompt "
                  "as a change of content; recheck the earlier one first")
        return _range_answer(
            memory_id, start, end, [],
            gaps=[_gap(earlier, "incomparable")], note=reason)

    version = await register(pool, CHANGE_GENERATOR, extractor)

    cached = await pool.fetchrow(
        """
        SELECT r.range_id, r.outcome, r.reason, r.change_artifact_id,
               a.fields
        FROM memory_change_ranges r
        LEFT JOIN artifacts a
               ON a.artifact_id = r.change_artifact_id AND a.deleted_at IS NULL
        WHERE r.memory_id = $1 AND r.from_checkpoint_id = $2
          AND r.to_checkpoint_id = $3 AND r.generator_version = $4
        """,
        memory_id, start["checkpoint_id"], end["checkpoint_id"], version)
    if cached is not None:
        return _range_answer(memory_id, start, end, _changes_of(cached["fields"]),
                             artifact_id=cached["change_artifact_id"],
                             cached=True)

    spec = GENERATORS[CHANGE_GENERATOR]
    text = "EARLIER:\n" + _render(earlier) + "\n\nLATER:\n" + _render(later)
    try:
        envelope = await extractor.extract(
            text[:200_000], data_type=spec["data_type"], prompt=spec["prompt"])
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        await pool.execute(
            """
            INSERT INTO memory_change_ranges (range_id, org_id, project_id,
                memory_id, from_checkpoint_id, to_checkpoint_id,
                generator_version, status, reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'failed', $8)
            ON CONFLICT DO NOTHING
            """,
            new_id("rng"), org_id, head["project_id"], memory_id,
            start["checkpoint_id"], end["checkpoint_id"], version, str(exc)[:500])
        raise

    changes = (getattr(envelope, "fields", None) or {}).get("changes") or []

    # Sources are the two RECORDS, not the two descriptions -- what makes
    # erasing either one cascade onto the answer built from it, and what a
    # citation has to be able to open. The same rule `run_check` follows.
    members = [
        {"data_id": row["data_id"], "access_level": row["access_level"],
         "shared_with": row["shared_with"], "owner_id": row["owner_id"]}
        for row in (later, earlier)
    ]
    async with pool.acquire() as conn, conn.transaction():
        artifact_id = await store_artifact(
            conn, org_id=org_id, project_id=head["project_id"],
            kind=CHANGE_GENERATOR, label=spec["label"], envelope=envelope,
            model_id=getattr(extractor, "model_id", None), version=version,
            members=members,
            offsets=[(m["data_id"], 0, 0) for m in members if m["data_id"]])
        await conn.execute(
            """
            INSERT INTO memory_change_ranges (range_id, org_id, project_id,
                memory_id, from_checkpoint_id, to_checkpoint_id,
                generator_version, change_artifact_id, status, outcome)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'complete', $9)
            ON CONFLICT (memory_id, from_checkpoint_id, to_checkpoint_id,
                         generator_version) DO NOTHING
            """,
            new_id("rng"), org_id, head["project_id"], memory_id,
            start["checkpoint_id"], end["checkpoint_id"], version, artifact_id,
            # An empty list is the answer for a span that did not move, and it is
            # stored as one. "Compared and found nothing" and "never compared"
            # are different claims -- the whole question the timeline exists to
            # answer, asked at range scale.
            "changed" if changes else "unchanged")
        await record_audit(
            conn, principal, action="checkpoint.range_compared",
            project_id=head["project_id"], target_type="memory",
            target_id=memory_id,
            detail={"from": start["checkpoint_id"], "to": end["checkpoint_id"],
                    "changes": len(changes)})

    return _range_answer(memory_id, start, end, changes, artifact_id=artifact_id)


def _changes_of(fields) -> list:
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            return []
    return [c for c in ((fields or {}).get("changes") or []) if isinstance(c, dict)]


def _range_answer(memory_id, start, end, changes, *, gaps=None, note=None,
                  artifact_id=None, cached=False) -> dict:
    """A net range in the shape a composed one already has.

    One shape for both readings, so a caller switching `net` on does not have to
    parse a different response -- `basis` is what tells them which question was
    answered, and it is the only field whose value differs by construction.

    Net rows carry no per-row `seq`, and that is deliberate rather than an
    omission: a net claim is about the span, not about a point inside it, and
    stamping one of the two endpoints onto it would invite exactly the
    misreading the two bases exist to prevent.
    """
    kinds = [c.get("kind") for c in changes]
    answer = {
        "memory_id": memory_id, "basis": "net",
        "from": start, "to": end,
        "checkpoints": (end["seq"] - start["seq"]) if (start and end) else 0,
        "changes": changes,
        "counts": {"added": kinds.count("added"), "removed": kinds.count("removed"),
                   "changed": kinds.count("changed")},
        "gaps": gaps or [],
        # The whole answer, for a caller that wants the artifact rather than the
        # rendering -- and the thing a citation opens.
        "change_artifact_id": artifact_id,
        # Whether this cost a model call. Beside the answer rather than in a
        # bill next month, which is the rule the console already follows for
        # anything that spends.
        "cached": cached,
    }
    if note:
        answer["note"] = note
    return answer
