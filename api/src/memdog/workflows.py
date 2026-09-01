"""Long-running state machine instances, as a system of record.

The engine lives outside memdog. It calls in to record input and is told when
state changes; memdog holds the state, the history and the deadlines and never
executes anything. Every decision here follows from that split.

**A directed state graph that may contain cycles**, not a DAG — the A in DAG is
*acyclic*, and `review → changes_requested → review` is a legitimate workflow
that a DAG cannot express. Three things follow, and each one shows up in the
code below:

- **No topological order**, so progress cannot be measured as depth and `stuck`
  is not derivable from position. It needs a deadline.
- **Termination is not guaranteed.** An infinite loop is indistinguishable from
  a long legitimate one except by a budget, so instances carry one.
- **Re-entry.** A state can be visited many times, so *"when did this enter
  blocked"* has many answers and the **history is the record** — `current_state`
  is a cache that `verify` re-folds rather than a fact anyone has to trust.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
from pydantic import BaseModel, Field, ValidationError

from .audit import record_audit
from .auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, Principal
from .events import emit
from .ids import new_id
from .telemetry import span

log = logging.getLogger(__name__)

# The clock's own trigger. Prefixed so a caller can never define an input that
# impersonates a timeout -- "the deadline moved it" and "somebody moved it" are
# the two facts a history exists to keep apart.
TIMEOUT_TRIGGER = "@timeout"

ACTOR_KINDS = ("human", "key", "system")


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Conflict(WorkflowError):
    """Somebody else moved this instance first.

    Carries the current state so the caller can decide rather than re-read: a
    409 that says only *no* forces a second round trip to learn the thing the
    server already knew.
    """

    def __init__(self, message: str, *, current_state: str, current_seq: int) -> None:
        super().__init__(message, status=409)
        self.current_state = current_state
        self.current_seq = current_seq


# --------------------------------------------------------------- definition


class StateSpec(BaseModel):
    """A state, and what happens if nothing does.

    `ttl_seconds` without `on_timeout` is refused at validation: a deadline with
    nowhere to go would fire forever, which is a worse failure than no deadline.
    """

    ttl_seconds: int | None = None
    on_timeout: str | None = None
    terminal: bool = False


class TransitionSpec(BaseModel):
    from_state: str = Field(alias="from")
    on: str
    to: str
    # Guards. `requires_actor_kind` is what keeps a robot from performing an
    # approval a human is supposed to perform -- the difference between a
    # workflow and a script.
    requires_actor_kind: str | None = None
    emit: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class WorkflowConfig(BaseModel):
    initial: str
    states: dict[str, StateSpec]
    transitions: list[TransitionSpec]


def validate_config(raw: dict) -> WorkflowConfig:
    """Reject at definition time what would otherwise be a stuck instance.

    Everything here is a mistake that is cheap to catch now and expensive
    later: an instance already sitting in a state with no exit cannot be fixed
    by editing the definition, because it pinned the version it started on.

    **Cycles are allowed** — that is the point of the primitive — so this must
    not be a DAG check. What it rejects is a graph that cannot be *entered* or
    cannot be *left*.
    """
    try:
        config = WorkflowConfig.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowError(f"invalid workflow config: {exc.errors()[0]['msg']}") from exc

    if config.initial not in config.states:
        raise WorkflowError(f"initial state {config.initial!r} is not one of the states")

    for spec in config.transitions:
        for name, role in ((spec.from_state, "from"), (spec.to, "to")):
            if name not in config.states:
                raise WorkflowError(f"transition {role} {name!r} is not a declared state")
        if spec.requires_actor_kind and spec.requires_actor_kind not in ACTOR_KINDS:
            raise WorkflowError(
                f"requires_actor_kind must be one of {', '.join(ACTOR_KINDS)}")

    for name, spec in config.states.items():
        if spec.ttl_seconds is not None and not spec.on_timeout:
            raise WorkflowError(
                f"state {name!r} has a ttl and no on_timeout: a deadline with nowhere "
                "to go fires forever")
        if spec.on_timeout and spec.on_timeout not in config.states:
            raise WorkflowError(f"on_timeout target {spec.on_timeout!r} is not a state")
        if spec.terminal and any(t.from_state == name for t in config.transitions):
            raise WorkflowError(
                f"state {name!r} is terminal and has outgoing transitions; one of the "
                "two is wrong and an instance would sit in it either way")

    # Reachability, walked forward from the initial state. An unreachable state
    # is not an error a running instance would ever hit -- it is a sign the
    # graph says something its author did not mean, and saying so at definition
    # time costs nothing.
    reachable = {config.initial}
    frontier = [config.initial]
    while frontier:
        here = frontier.pop()
        outgoing = [t.to for t in config.transitions if t.from_state == here]
        timeout = config.states[here].on_timeout
        for nxt in outgoing + ([timeout] if timeout else []):
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    orphans = sorted(set(config.states) - reachable)
    if orphans:
        raise WorkflowError(
            f"unreachable from {config.initial!r}: {', '.join(orphans)}")
    return config


async def upsert_definition(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, external_id: str,
    name: str, config: dict, description: str | None = None,
    max_transitions: int = 1000,
) -> dict:
    """Define a workflow, or redefine it.

    A redefinition bumps `config_version` and **does not touch running
    instances**: each pinned the version it started on, and moving a thousand
    live instances onto a graph they never entered is the failure this pin
    exists to prevent. Moving one deliberately is `migrate`.
    """
    principal.require(CONFIG_WRITE)
    validate_config(config)

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO workflow_definitions (definition_id, org_id, project_id,
                external_id, name, description, config, max_transitions)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (project_id, external_id) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    config = EXCLUDED.config,
                    max_transitions = EXCLUDED.max_transitions,
                    config_version = workflow_definitions.config_version + 1,
                    updated_at = now()
            RETURNING definition_id, config_version,
                      (xmax = 0) AS created
            """,
            new_id("wfd"), principal.org_id, project_id, external_id, name,
            description, json.dumps(config), max_transitions,
        )
        await record_audit(
            conn, principal,
            action="workflow.defined" if row["created"] else "workflow.redefined",
            project_id=project_id, target_type="workflow", target_id=row["definition_id"],
            detail={"external_id": external_id, "version": row["config_version"]},
        )
    return {"definition_id": row["definition_id"], "external_id": external_id,
            "config_version": row["config_version"], "created": row["created"]}


async def list_definitions(
    pool: asyncpg.Pool, principal: Principal, project_id: str
) -> list[dict]:
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT d.definition_id, d.external_id, d.name, d.description, d.config,
               d.config_version, d.max_transitions, d.created_at,
               (SELECT count(*) FROM workflow_instances i
                 WHERE i.definition_id = d.definition_id AND i.status = 'running')
                 AS running
          FROM workflow_definitions d
         WHERE d.project_id = $1 AND d.org_id = $2 AND d.deleted_at IS NULL
         ORDER BY d.created_at DESC
        """,
        project_id, principal.org_id,
    )
    return [{**dict(r), "config": _loads(r["config"])} for r in rows]


def _loads(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


# ----------------------------------------------------------------- instance


async def start_instance(
    pool: asyncpg.Pool, principal: Principal, definition_id: str, *,
    external_id: str, case_id: str | None = None, payload: dict | None = None,
    access_level: str = "private", shared_with: list[str] | None = None,
) -> dict:
    """Begin one. Idempotent on `external_id`.

    An engine retrying a start after a timeout must not create a second
    instance of the same purchase order -- the retry is the ordinary case, not
    the exception, and making the caller deduplicate is making every caller
    solve it separately.
    """
    principal.require(DATA_WRITE)
    definition = await _definition(pool, principal, definition_id)
    config = validate_config(_loads(definition["config"]))

    existing = await pool.fetchrow(
        """
        SELECT instance_id, current_state, current_seq, status FROM workflow_instances
         WHERE project_id = $1 AND definition_id = $2 AND external_id = $3
        """,
        definition["project_id"], definition_id, external_id,
    )
    if existing is not None:
        return {**dict(existing), "created": False}

    instance_id = new_id("wfi")
    deadline = _deadline_for(config, config.initial)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO workflow_instances (instance_id, org_id, project_id,
                definition_id, definition_version, external_id, case_id,
                current_state, deadline_at, access_level, shared_with, owner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    now() + make_interval(secs => $9), $10, $11, $12)
            """,
            instance_id, definition["org_id"], definition["project_id"], definition_id,
            definition["config_version"], external_id, case_id, config.initial,
            deadline, access_level, json.dumps(shared_with or []), principal.user_id,
        )
        # Seq 0 is the start itself, recorded like any other transition. An
        # instance whose history begins at its first *input* cannot answer
        # "when did this begin" without a second column that means the same
        # thing.
        await conn.execute(
            """
            INSERT INTO workflow_transitions (transition_id, instance_id, seq,
                from_state, to_state, trigger, actor_user_id, actor_key_id,
                actor_kind, payload)
            VALUES ($1, $2, 0, '', $3, '@start', $4, $5, $6, $7)
            """,
            new_id("wft"), instance_id, config.initial, principal.user_id,
            principal.key_id, _actor_kind(principal), json.dumps(payload or {}),
        )
        await record_audit(
            conn, principal, action="workflow.started",
            project_id=definition["project_id"], target_type="workflow_instance",
            target_id=instance_id,
            detail={"definition_id": definition_id, "external_id": external_id},
        )
    return {"instance_id": instance_id, "current_state": config.initial,
            "current_seq": 0, "status": "running", "created": True}


def _actor_kind(principal: Principal) -> str:
    """Who is acting, in the three kinds a guard can name.

    A credential acting on its own is a `key`; a person, however they
    authenticated, is `human`. `system` is reserved for the clock and is not
    reachable from a request -- otherwise a caller could claim to be a timeout.
    """
    return "key" if principal.key_id else "human"


def _deadline_for(config: WorkflowConfig, state: str) -> float | None:
    spec = config.states.get(state)
    return float(spec.ttl_seconds) if spec and spec.ttl_seconds else None


async def _definition(pool, principal: Principal, definition_id: str):
    row = await pool.fetchrow(
        "SELECT * FROM workflow_definitions WHERE definition_id = $1 AND deleted_at IS NULL",
        definition_id)
    if row is None or row["org_id"] != principal.org_id:
        raise WorkflowError("workflow not found", status=404)
    return row


async def _instance(pool, principal: Principal, instance_id: str):
    row = await pool.fetchrow(
        "SELECT * FROM workflow_instances WHERE instance_id = $1", instance_id)
    if row is None or row["org_id"] != principal.org_id:
        raise WorkflowError("instance not found", status=404)
    return row


# -------------------------------------------------------------- the one verb


async def apply_input(
    pool: asyncpg.Pool, principal: Principal, instance_id: str, *,
    trigger: str, expected_seq: int | None = None, data_id: str | None = None,
    payload: dict | None = None, actor_kind: str | None = None,
) -> dict:
    """The only thing that moves an instance, and the whole of the concurrency story.

    One transaction, and the update is **conditional on the sequence the caller
    believed it was acting on**. Two actors racing on one instance therefore
    resolve as one winner and one `409` carrying the state it actually found —
    rather than two transitions out of the same state, which is the corruption
    a state machine cannot survive and cannot detect afterwards.

    `expected_seq` is optional because a single-writer engine does not need it;
    omitting it means *whatever it is now*, which is the right default for the
    common case and the wrong one for a race the caller knows it is in.
    """
    if trigger == TIMEOUT_TRIGGER and actor_kind != "system":
        # The clock's trigger is not addressable from a request. Otherwise a
        # caller could claim a deadline fired and the history could never tell
        # the difference.
        raise WorkflowError(f"{TIMEOUT_TRIGGER} is the clock's, not a caller's", status=403)
    if actor_kind is None:
        principal.require(DATA_WRITE)
        actor_kind = _actor_kind(principal)

    with span("workflow.input", instance_id=instance_id, trigger=trigger):
        return await _apply(
            pool, principal, instance_id, trigger=trigger, expected_seq=expected_seq,
            data_id=data_id, payload=payload or {}, actor_kind=actor_kind)


async def _apply(
    pool, principal: Principal, instance_id: str, *, trigger: str,
    expected_seq: int | None, data_id: str | None, payload: dict, actor_kind: str,
) -> dict:
    instance = await _instance(pool, principal, instance_id)
    if instance["status"] != "running":
        raise WorkflowError(
            f"this instance is {instance['status']}, and a closed instance does not move",
            status=409)

    definition = await pool.fetchrow(
        "SELECT * FROM workflow_definitions WHERE definition_id = $1",
        instance["definition_id"])
    config = validate_config(_loads(definition["config"]))
    state = instance["current_state"]

    spec = next(
        (t for t in config.transitions if t.from_state == state and t.on == trigger), None)
    if spec is None and trigger == TIMEOUT_TRIGGER:
        target = config.states[state].on_timeout
        spec = TransitionSpec(**{"from": state, "on": TIMEOUT_TRIGGER, "to": target}) \
            if target else None
    if spec is None:
        raise WorkflowError(
            f"{trigger!r} is not accepted in {state!r}; "
            f"from here: {', '.join(sorted(t.on for t in config.transitions
                                           if t.from_state == state)) or 'nothing'}",
            status=409)
    if spec.requires_actor_kind and spec.requires_actor_kind != actor_kind:
        raise WorkflowError(
            f"{trigger!r} requires a {spec.requires_actor_kind} actor and this is a "
            f"{actor_kind}", status=403)

    # The budget, which is the only thing distinguishing a legitimate cycle from
    # a loop that will never stop. Recorded as `errored` rather than refused
    # silently: an instance that stops moving with no reason is the state
    # nobody can diagnose.
    if instance["transition_count"] + 1 > definition["max_transitions"]:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE workflow_instances SET status = 'errored', closed_at = now(), "
                "updated_at = now() WHERE instance_id = $1", instance_id)
        raise WorkflowError(
            f"this instance has made {instance['transition_count']} transitions, its "
            f"budget of {definition['max_transitions']}: a cycle that does not terminate "
            "is indistinguishable from one that has not yet, except by this number",
            status=409)

    next_seq = instance["current_seq"] + 1
    deadline = _deadline_for(config, spec.to)
    terminal = config.states[spec.to].terminal

    async with pool.acquire() as conn, conn.transaction():
        # Conditional on the sequence. This single statement is the whole of
        # the optimistic-concurrency design: the loser updates zero rows and
        # learns what it lost to.
        moved = await conn.fetchrow(
            """
            UPDATE workflow_instances
               SET current_state = $2, current_seq = $3,
                   deadline_at = CASE WHEN $4::float IS NULL THEN NULL
                                      ELSE now() + make_interval(secs => $4) END,
                   transition_count = transition_count + 1,
                   status = CASE WHEN $5 THEN 'completed' ELSE status END,
                   closed_at = CASE WHEN $5 THEN now() ELSE closed_at END,
                   updated_at = now()
             WHERE instance_id = $1 AND current_seq = $6
            RETURNING current_state, current_seq, status
            """,
            instance_id, spec.to, next_seq, deadline, terminal,
            expected_seq if expected_seq is not None else instance["current_seq"],
        )
        if moved is None:
            current = await pool.fetchrow(
                "SELECT current_state, current_seq FROM workflow_instances "
                "WHERE instance_id = $1", instance_id)
            raise Conflict(
                "this instance moved while you were deciding",
                current_state=current["current_state"], current_seq=current["current_seq"])

        await conn.execute(
            """
            INSERT INTO workflow_transitions (transition_id, instance_id, seq,
                from_state, to_state, trigger, actor_user_id, actor_key_id,
                actor_kind, data_id, payload)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            new_id("wft"), instance_id, next_seq, state, spec.to, trigger,
            principal.user_id, principal.key_id, actor_kind, data_id,
            json.dumps(payload),
        )
        if data_id:
            # The input, as memory. An instance that says "approved" without
            # what was approved is a state machine, not a record.
            await conn.execute(
                """
                INSERT INTO workflow_instance_members (instance_id, data_id, basis)
                VALUES ($1, $2, 'asserted') ON CONFLICT DO NOTHING
                """,
                instance_id, data_id)

        # Emitted inside the transaction, so "transitioned but never announced"
        # cannot happen. What the outside engine is told, and the only thing it
        # needs in order to act.
        await emit(
            conn, event_type="workflow.transitioned", org_id=instance["org_id"],
            project_id=instance["project_id"], data_id=data_id,
            payload={
                "instance_id": instance_id, "seq": next_seq,
                "from_state": state, "to_state": spec.to, "trigger": trigger,
                "actor_kind": actor_kind, "status": moved["status"],
                # What the definition said to call this, when it said anything.
                # The transition declares what it emits; where it goes is a
                # subscription's business and deliberately not the graph's.
                "event": (spec.emit or {}).get("event"),
            },
        )
    return {"instance_id": instance_id, "from_state": state,
            "current_state": moved["current_state"], "current_seq": moved["current_seq"],
            "status": moved["status"], "trigger": trigger}


# ------------------------------------------------------------------- read


async def get_state(pool: asyncpg.Pool, principal: Principal, instance_id: str) -> dict:
    principal.require(DATA_READ)
    row = await _instance(pool, principal, instance_id)
    return {
        "instance_id": row["instance_id"], "definition_id": row["definition_id"],
        "definition_version": row["definition_version"], "external_id": row["external_id"],
        "case_id": row["case_id"], "current_state": row["current_state"],
        "current_seq": row["current_seq"], "status": row["status"],
        "deadline_at": row["deadline_at"], "transition_count": row["transition_count"],
        # Said rather than left to be computed: an overdue instance is the one
        # thing a dashboard exists to surface, and `stuck` is not derivable
        # from position in a graph that has no topological order.
        "overdue": bool(row["deadline_at"] and row["deadline_at"] < _now()),
        "created_at": row["created_at"], "closed_at": row["closed_at"],
    }


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def history(
    pool: asyncpg.Pool, principal: Principal, instance_id: str, *, limit: int = 200
) -> dict:
    """Newest first, because the question is almost always *what just happened*."""
    principal.require(DATA_READ)
    await _instance(pool, principal, instance_id)
    rows = await pool.fetch(
        """
        SELECT transition_id, seq, from_state, to_state, trigger, actor_kind,
               actor_user_id, actor_key_id, data_id, payload, occurred_at
          FROM workflow_transitions
         WHERE instance_id = $1
         ORDER BY seq DESC
         LIMIT $2
        """,
        instance_id, limit,
    )
    return {
        "instance_id": instance_id,
        "transitions": [{**dict(r), "payload": _loads(r["payload"])} for r in rows],
        "capped": len(rows) == limit,
    }


async def verify_state(pool: asyncpg.Pool, principal: Principal, instance_id: str) -> dict:
    """Re-fold the log and compare it to the cache.

    `current_state` is a denormalisation, and a denormalisation nobody can check
    is one people stop trusting the first time something looks wrong. This walks
    the transitions in order and reports whether the column agrees — cheap,
    because an instance's history is bounded by its own budget.
    """
    principal.require(DATA_READ)
    instance = await _instance(pool, principal, instance_id)
    rows = await pool.fetch(
        "SELECT seq, from_state, to_state FROM workflow_transitions "
        "WHERE instance_id = $1 ORDER BY seq", instance_id)

    folded, gaps, breaks = None, [], []
    for index, row in enumerate(rows):
        if row["seq"] != index:
            gaps.append(row["seq"])
        if folded is not None and row["from_state"] != folded:
            breaks.append(row["seq"])
        folded = row["to_state"]
    return {
        "instance_id": instance_id,
        "folded_state": folded,
        "cached_state": instance["current_state"],
        "agrees": folded == instance["current_state"]
                  and instance["current_seq"] == (rows[-1]["seq"] if rows else 0),
        # A gap means a transition was lost; a break means two transitions
        # disagree about where the instance was. They are different failures
        # and only one of them is recoverable by replaying.
        "sequence_gaps": gaps,
        "chain_breaks": breaks,
        "transitions": len(rows),
    }


async def list_instances(
    pool: asyncpg.Pool, principal: Principal, project_id: str, *,
    state: str | None = None, definition_id: str | None = None,
    overdue: bool = False, limit: int = 100,
) -> list[dict]:
    """The dashboard query: what is running, what is stuck, what is overdue."""
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT i.instance_id, i.external_id, i.current_state, i.current_seq, i.status,
               i.deadline_at, i.transition_count, i.case_id, i.created_at,
               d.external_id AS definition, d.name AS definition_name,
               (i.deadline_at IS NOT NULL AND i.deadline_at < now()) AS overdue
          FROM workflow_instances i
          JOIN workflow_definitions d ON d.definition_id = i.definition_id
         WHERE i.project_id = $1 AND i.org_id = $2
           AND ($3::text IS NULL OR i.current_state = $3)
           AND ($4::text IS NULL OR i.definition_id = $4)
           AND (NOT $5 OR (i.deadline_at IS NOT NULL AND i.deadline_at < now()
                           AND i.status = 'running'))
         ORDER BY i.updated_at DESC
         LIMIT $6
        """,
        project_id, principal.org_id, state, definition_id, overdue, limit,
    )
    return [dict(r) for r in rows]


# -------------------------------------------------------------------- clock


async def tick(pool: asyncpg.Pool, *, limit: int = 100) -> dict:
    """Move everything whose deadline has passed.

    The only source of `system` transitions, and the reason a deadline is a
    real mechanism rather than a column nobody reads: in a graph with no
    topological order, a deadline is the *only* way to distinguish an instance
    that is progressing slowly from one that is stuck.

    `FOR UPDATE SKIP LOCKED` because two ticks overlapping must not both fire
    the same timeout -- and the conditional update in `_apply` would catch it
    anyway, which is the belt and the braces being different mechanisms.
    """
    due = await pool.fetch(
        """
        SELECT i.instance_id, i.org_id, i.project_id, i.owner_id
          FROM workflow_instances i
         WHERE i.status = 'running' AND i.deadline_at IS NOT NULL
           AND i.deadline_at <= now()
         ORDER BY i.deadline_at
         LIMIT $1
         FOR UPDATE SKIP LOCKED
        """,
        limit,
    )
    fired, conflicts, failed = 0, 0, 0
    for row in due:
        # As the instance's owner, so the transition is attributable and the
        # ACL checks in `_instance` mean what they mean everywhere else.
        actor = Principal(
            user_id=row["owner_id"], org_id=row["org_id"],
            capabilities=frozenset({DATA_WRITE, DATA_READ}),
            project_id=row["project_id"], mode="workflow",
        )
        try:
            await apply_input(
                pool, actor, row["instance_id"], trigger=TIMEOUT_TRIGGER,
                actor_kind="system")
            fired += 1
        except Conflict:
            # Somebody moved it between the select and the update, which is the
            # ordinary case rather than an error: the input beat the clock and
            # the deadline is no longer the truth about this instance.
            conflicts += 1
        except WorkflowError as exc:
            log.warning("timeout for %s did not apply: %s", row["instance_id"], exc)
            failed += 1
    return {"due": len(due), "fired": fired, "raced": conflicts, "failed": failed}
