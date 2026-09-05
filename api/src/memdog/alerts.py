"""Alerts: declare what is worth knowing about, and record it when it happens.

Three properties carry the design, and each is a response to something that
would otherwise go wrong quietly.

**Evaluation is per window, never per write.** N alerts by M writes means every
write pays for every alert, and one crawl importing ten thousand items would
trigger ten thousand rounds -- in `llm` mode, ten thousand model calls for a
question nobody asked urgently. The consumer coalesces and evaluates once over
the batch.

**The watermark is the record, not the queue.** Transitions are `domain_events`
rows and an alert reads forward from a `sequence`, so a message lost between
publish and handler costs latency rather than an alert. This is the same
relation the reconciler already has to dispatch.

**Nothing here stores an ACL.** Visibility belongs to the subject and is
resolved when someone reads or a delivery is sent, against their rights at that
moment. A copy taken at match time is stale the moment the subject is re-shared,
and ignores a revocation in between -- and a notification is a side channel
around every other access check, so it is the one place that cannot be tolerated.
"""

from __future__ import annotations

import json

import asyncpg

from .acl import visibility_params
from .auth import CONFIG_WRITE, DATA_READ, Principal
from .config import load_settings
from .ids import new_id
import logging

from .telemetry import span

# What each surface is, and which fields its selector may name. Closed on
# purpose: an open vocabulary degrades into unqueryable free text, and the value
# of a typed condition is being able to ask which alerts watch a field.
SURFACES: dict[str, frozenset[str]] = {
    "fact.asserted":   frozenset({"predicate", "basis", "subject_type", "object_type",
                                  "subject_name", "object_name"}),
    "fact.superseded": frozenset({"predicate", "basis", "subject_type", "object_type",
                                  "subject_name", "object_name", "replaced_by_name"}),
    "fact.retracted":  frozenset({"predicate", "basis", "subject_name", "object_name"}),
    "data.revised":    frozenset({"source", "data_type", "producer_id"}),
    "memory.member_added": frozenset({"memory_type", "added_by"}),
    "memory.retyped":  frozenset({"from_type", "to_type"}),
    "case.member_promoted": frozenset({"case_type"}),
    # The from/to pair is why capture has to be synchronous: once the row reads
    # its new level the old one is gone, and no sweep afterwards recovers it.
    "acl.changed":     frozenset({"from_level", "to_level", "data_type"}),
}

# Which subject a surface's visibility is asked of. An event stores no ACL, so
# this is how a read resolves one -- against the subject, now, rather than a
# copy taken when the alert matched.
SUBJECT_OF: dict[str, str] = {
    "fact.asserted": "fact", "fact.superseded": "fact", "fact.retracted": "fact",
    "data.revised": "item", "acl.changed": "item", "case.member_promoted": "item",
    "memory.member_added": "memory", "memory.retyped": "memory",
}

# Editing any of these changes what matches, so it invalidates the backtest.
# Renaming an alert does not.
# Enough to judge a selector by eye without turning the response into a dump.
SAMPLE_LIMIT = 25

# What an alert may be scoped to. Each is a join rather than a payload field:
# a transition does not know which memory its item is in, and denormalising that
# onto the event would go stale the moment somebody moved it.
SCOPES = {
    "memory_id":   "only items in this memory",
    "case_id":     "only items on this case",
    "producer_id": "only what this source wrote",
    "entity_id":   "only claims about this entity",
}

MATCHING_FIELDS = ("mode", "surface", "where_clause", "describe", "model_id",
                   "scope")


log = logging.getLogger(__name__)


class AlertError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def validate_scope(scope: dict) -> None:
    if not isinstance(scope, dict):
        raise AlertError("scope must be an object")
    unknown = sorted(set(scope) - set(SCOPES))
    if unknown:
        raise AlertError(
            f"cannot scope by {', '.join(unknown)}; "
            f"available: {', '.join(sorted(SCOPES))}")


async def in_scope(
    pool, scope: dict, candidates: list[tuple],
) -> set[int]:
    """Which candidates survive the scope, by sequence.

    One query per scope key over the whole batch rather than one per candidate:
    the batch is already bounded by `batch_cap`, and a join per event would put
    five hundred round trips inside a run that is supposed to be cheap.

    An empty scope admits everything, which is the common case and not a
    degraded one.
    """
    if not scope:
        return {c[0]["sequence"] for c in candidates}

    surviving = {c[0]["sequence"] for c in candidates}
    # Which records each candidate stands on. A container scope is a question
    # about records, so an event with none can never survive one.
    records: dict[int, set[str]] = {
        c[0]["sequence"]: {c[0]["data_id"]} for c in candidates if c[0]["data_id"]
    }

    # A fact is not an item, so `fact.*` events carry no `data_id` at all -- and
    # scoping one to a memory therefore intersected with an empty set and
    # matched nothing, forever, with no error and no empty state. An alert on
    # "a reading of this book was replaced" looked exactly like a quiet week.
    #
    # A derived fact does stand on records: its edges carry the evidence. One
    # query for the batch, and a fact survives if *any* of its evidence is in
    # the container -- the same rule a person would apply reading it. An
    # asserted fact has no evidence and correctly survives no container scope;
    # `entity_id` is the scope that reaches it.
    unbacked = {
        c[0]["sequence"]: c[1].get("fact_id")
        for c in candidates
        if not c[0]["data_id"] and c[1].get("fact_id")
    }
    if unbacked:
        evidence = await pool.fetch(
            "SELECT fact_id, source_data_id FROM entity_edges "
            "WHERE fact_id = ANY($1::text[])",
            sorted(set(unbacked.values())),
        )
        by_fact: dict[str, set[str]] = {}
        for row in evidence:
            by_fact.setdefault(row["fact_id"], set()).add(row["source_data_id"])
        for sequence, fact_id in unbacked.items():
            if by_fact.get(fact_id):
                records[sequence] = by_fact[fact_id]

    data_ids = sorted({d for ds in records.values() for d in ds})

    if scope.get("entity_id"):
        # Facts carry their endpoints, so this one needs no join at all.
        wanted = scope["entity_id"]
        surviving &= {
            seq for seq, payload in ((c[0]["sequence"], c[1]) for c in candidates)
            if payload.get("subject_id") == wanted or payload.get("object_id") == wanted
        }

    async def _allowed(sql: str, arg) -> set[str]:
        rows = await pool.fetch(sql, arg, data_ids)
        return {r["data_id"] for r in rows}

    if scope.get("memory_id"):
        # Through the hierarchy, not just the one container.
        #
        # A scope of one memory was single-level, so an alert on a parent never
        # saw a child's changes -- and a scope that silently means less than it
        # says is the same failure as one that silently means more. Resolved by
        # reading `part_of` downward at match time: no new writes, no duplicated
        # events, no propagation storm, and no cycle to worry about in the write
        # path because nothing is being written. `link` refuses the cycle at the
        # edge that would close it, so the walk terminates.
        from .memories import contained_memories

        scoped = await contained_memories(pool, scope["memory_id"])
        rows = await pool.fetch(
            "SELECT data_id FROM memory_members WHERE memory_id = ANY($1::text[]) "
            "AND data_id = ANY($2::text[])", scoped, data_ids)
        allowed = {r["data_id"] for r in rows}
        surviving &= {s for s, ds in records.items() if ds & allowed}
    if scope.get("case_id"):
        allowed = await _allowed(
            "SELECT data_id FROM case_members WHERE case_id = $1 "
            "AND data_id = ANY($2::text[])", scope["case_id"])
        surviving &= {s for s, ds in records.items() if ds & allowed}
    if scope.get("producer_id"):
        allowed = await _allowed(
            "SELECT data_id FROM data_items WHERE producer_id = $1 "
            "AND data_id = ANY($2::text[])", scope["producer_id"])
        surviving &= {s for s, ds in records.items() if ds & allowed}
    return surviving


def validate(*, mode: str, surface: str, where_clause: dict, describe: str | None) -> None:
    if surface not in SURFACES:
        raise AlertError(
            f"unknown surface {surface!r}; known: {', '.join(sorted(SURFACES))}")
    if mode not in ("rule", "llm"):
        raise AlertError("mode must be 'rule' or 'llm'")
    if not isinstance(where_clause, dict):
        raise AlertError("where must be an object of field -> allowed values")
    # A path is allowed if its *root* is a field this surface emits. Anything
    # under it is free -- payload shapes evolve, and a selector that cannot
    # reach a nested value is not generic. But an unknown root is almost always
    # a typo, and silently never matching is the worst way to find that out.
    known = SURFACES[surface]
    for path, condition in where_clause.items():
        root = path.split(".")[0]
        if root not in known:
            raise AlertError(
                f"{surface} emits no {root!r}; it has: {', '.join(sorted(known))}")
        op = condition.get("op", "in") if isinstance(condition, dict) else "in"
        if op not in OPERATORS:
            raise AlertError(
                f"unknown operator {op!r}; use one of: {', '.join(sorted(OPERATORS))}")
        arg = condition.get("value") if isinstance(condition, dict) else condition
        if op not in ("exists", "changed") and (arg is None or arg == []):
            raise AlertError(f"{path} needs a value to compare against")
    if mode == "llm":
        if not describe:
            raise AlertError("llm mode needs a description of the event")
        # A model against every transition in the project is unbounded in
        # exactly the way nobody notices until the bill arrives.
        if not where_clause:
            raise AlertError(
                "llm mode needs a selector as well; without one every "
                "transition in the project reaches the model")



# The operators a condition may use. Deliberately small and total: every one is
# decidable against a value that may be absent, so a missing field is `false`
# rather than an error that stalls a batch on one odd row.
OPERATORS = {
    "in":        lambda v, arg: v in arg,
    "not_in":    lambda v, arg: v not in arg,
    "eq":        lambda v, arg: v == (arg[0] if isinstance(arg, list) else arg),
    "ne":        lambda v, arg: v != (arg[0] if isinstance(arg, list) else arg),
    "exists":    lambda v, arg: (v is not None) is bool(arg),
    "contains":  lambda v, arg: any(str(a).lower() in str(v).lower() for a in arg),
    "gt":        lambda v, arg: _num(v) is not None and _num(v) > _num(arg),
    "lt":        lambda v, arg: _num(v) is not None and _num(v) < _num(arg),
    "changed":   lambda v, arg: True,   # the surface already means "it changed"
}


def _num(value):
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dig(payload: dict, path: str):
    """Follow a dotted path, tolerating anything missing.

    Generic on purpose: a selector should be able to reach into a payload shape
    this module has never seen, so a surface added later needs no code here.
    `webhooks._dig` does the same for inbound mappings and for the same reason.
    """
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def matches_selector(where_clause: dict, payload: dict) -> bool:
    """Deterministic, and the whole of evaluation in `rule` mode.

    Two shapes, because the short one is what people write:

        {"predicate": ["located_in"]}                 -- implicitly `in`
        {"predicate": {"op": "in", "value": [...]}}   -- any operator

    Applied in Python rather than SQL because the batch is already bounded by
    `batch_cap` and already in memory; pushing it down would buy nothing and
    would make the vocabulary a second thing to keep in step.
    """
    for path, condition in where_clause.items():
        value = dig(payload, path)
        if isinstance(condition, dict):
            op, arg = condition.get("op", "in"), condition.get("value")
            # `{"op": "exists"}` means exists. It read as *absent*: the operator
            # compares against `bool(arg)`, `validate` deliberately allows the
            # value to be omitted for this operator, and omitting it therefore
            # inverted the condition -- silently, since a rule that matches
            # nothing looks exactly like a quiet week. Say it explicitly to
            # mean the opposite: `{"op": "exists", "value": false}`.
            if op == "exists" and "value" not in condition:
                arg = True
        else:
            op, arg = "in", condition
        check = OPERATORS.get(op)
        if check is None or not check(value, arg):
            return False
    return True


async def create_alert(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, name: str,
    surface: str, mode: str = "rule", where: dict | None = None,
    describe: str | None = None, model_id: str | None = None,
    debounce_seconds: int = 5, batch_cap: int = 500, scope: dict | None = None,
) -> dict:
    principal.require(CONFIG_WRITE)
    where, scope = where or {}, scope or {}
    validate(mode=mode, surface=surface, where_clause=where, describe=describe)
    validate_scope(scope)
    # Starts at the current head, never at zero: a new alert reports what
    # happens next, and firing a hundred notifications about last month the
    # moment someone saves a definition is how people switch alerts off.
    head = await pool.fetchval("SELECT coalesce(max(sequence), 0) FROM domain_events")
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO alerts (alert_id, org_id, project_id, name, mode, surface,
                where_clause, describe, model_id, debounce_seconds, batch_cap,
                watermark, scope)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12,
                    $13::jsonb)
            RETURNING *
            """,
            new_id("alr"), principal.org_id, project_id, name, mode, surface,
            json.dumps(where), describe, model_id, debounce_seconds, batch_cap, head,
            json.dumps(scope),
        )
    except asyncpg.UniqueViolationError as exc:
        # Names are unique per project so two alerts cannot be confused in a
        # feed. Reusing one is an ordinary mistake and deserves a sentence, not
        # a 500 with an empty body -- which is what a console shows a person who
        # simply typed a name they had used before.
        raise AlertError(
            f"an alert named {name!r} already exists in this project", status=409
        ) from exc
    return _public(row)


async def update_alert(
    pool: asyncpg.Pool, principal: Principal, alert_id: str, changes: dict,
) -> dict:
    """A change to what matches bumps `config_version` and un-approves the alert.

    Carrying a backtest forward onto a question that has changed is the same
    mistake as a crawler keeping its dry-run approval after its scope was
    edited, and it is worse here: the approval is the only thing standing
    between a rewritten description and a page at three in the morning.
    """
    principal.require(CONFIG_WRITE)
    current = await _owned(pool, principal, alert_id)
    merged = {
        "mode": changes.get("mode", current["mode"]),
        "surface": changes.get("surface", current["surface"]),
        "where_clause": changes.get("where", _loads(current["where_clause"])),
        "describe": changes.get("describe", current["describe"]),
        "model_id": changes.get("model_id", current["model_id"]),
        "scope": changes.get("scope", _loads(current["scope"])),
    }
    validate(mode=merged["mode"], surface=merged["surface"],
             where_clause=merged["where_clause"], describe=merged["describe"])
    validate_scope(merged["scope"])
    changed = any(
        merged[f] != (_loads(current[f]) if f in ("where_clause", "scope") else current[f])
        for f in MATCHING_FIELDS
    )
    row = await pool.fetchrow(
        """
        UPDATE alerts SET mode = $3, surface = $4, where_clause = $5::jsonb,
               describe = $6, model_id = $7, scope = $12::jsonb,
               name = coalesce($8, name),
               batch_cap = coalesce($9, batch_cap),
               debounce_seconds = coalesce($10, debounce_seconds),
               config_version = config_version + $11,
               -- Un-approved, and disabled with it. Leaving it enabled would
               -- keep firing a question nobody has looked at.
               backtested_version = CASE WHEN $11 = 1 THEN NULL ELSE backtested_version END,
               enabled = CASE WHEN $11 = 1 THEN false ELSE enabled END,
               updated_at = now()
         WHERE alert_id = $1 AND org_id = $2
        RETURNING *
        """,
        alert_id, principal.org_id, merged["mode"], merged["surface"],
        json.dumps(merged["where_clause"]), merged["describe"], merged["model_id"],
        changes.get("name"), changes.get("batch_cap"), changes.get("debounce_seconds"),
        1 if changed else 0, json.dumps(merged["scope"]),
    )
    return _public(row)


async def set_enabled(
    pool: asyncpg.Pool, principal: Principal, alert_id: str, enabled: bool,
) -> dict:
    """Enabling requires a backtest of *this* version.

    A description is a guess until it has been run against history, and a
    selector is easy to get subtly wrong. The gate is the crawler's dry-run gate
    with a different name.
    """
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, alert_id)
    if enabled and row["backtested_version"] != row["config_version"]:
        raise AlertError(
            "backtest this version before enabling it: an alert that has never "
            "been run against history is a guess", status=409)
    updated = await pool.fetchrow(
        "UPDATE alerts SET enabled = $3, updated_at = now() "
        "WHERE alert_id = $1 AND org_id = $2 RETURNING *",
        alert_id, principal.org_id, enabled,
    )
    return _public(updated)


async def list_alerts(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT a.*,
               (SELECT count(*) FROM observed_events o
                 WHERE o.alert_id = a.alert_id
                   AND o.occurred_at > now() - interval '24 hours') AS matches_24h,
               (SELECT max(started_at) FROM alert_runs r WHERE r.alert_id = a.alert_id) AS last_run_at,
               -- Transitions **of this alert's own kind** that it has not looked
               -- at. Counting the whole log instead was the first attempt and
               -- was useless: writes and enrichment flow constantly, so every
               -- alert showed a permanent backlog, and the number that was meant
               -- to say "the sweep has stopped" said nothing.
               (SELECT count(*) FROM domain_events e
                 WHERE e.sequence > a.watermark AND e.event_type = a.surface
                   AND e.org_id = a.org_id
                   AND (e.project_id = a.project_id OR e.project_id IS NULL)) AS behind
          FROM alerts a
         WHERE a.project_id = $1 AND a.org_id = $2 AND a.deleted_at IS NULL
         ORDER BY a.created_at DESC
        """,
        project_id, principal.org_id,
    )
    return [_public(r) for r in rows]


async def runs_for(
    pool: asyncpg.Pool, principal: Principal, alert_id: str, limit: int = 20,
) -> list[dict]:
    """What each evaluation actually did.

    `deferred` is the column this exists for: a run that hit its cap and said
    nothing would read as "nothing else matched".
    """
    principal.require(DATA_READ)
    await _owned(pool, principal, alert_id)
    rows = await pool.fetch(
        """
        SELECT run_id, trigger, status, config_version, from_sequence, to_sequence,
               candidates, matches, deferred, error, started_at, finished_at
          FROM alert_runs WHERE alert_id = $1
         ORDER BY started_at DESC LIMIT $2
        """,
        alert_id, limit,
    )
    return [dict(r) for r in rows]


async def delete_alert(pool: asyncpg.Pool, principal: Principal, alert_id: str) -> dict:
    principal.require(CONFIG_WRITE)
    await _owned(pool, principal, alert_id)
    await pool.execute(
        "UPDATE alerts SET deleted_at = now(), enabled = false WHERE alert_id = $1",
        alert_id,
    )
    return {"alert_id": alert_id, "deleted": True}


async def _owned(pool: asyncpg.Pool, principal: Principal, alert_id: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM alerts WHERE alert_id = $1 AND org_id = $2 AND deleted_at IS NULL",
        alert_id, principal.org_id,
    )
    if row is None:
        raise AlertError("alert not found", status=404)
    return row


def _loads(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _public(row) -> dict:
    d = dict(row)
    d["where"] = _loads(d.pop("where_clause", {}))
    d["scope"] = _loads(d.get("scope") or {})
    d.pop("deleted_at", None)
    return d


# -- evaluation -------------------------------------------------------------
#
# One function, called by the async consumer and by the tick. A reconciler that
# re-implements the thing it repairs drifts from it, and the drift shows up as a
# repair that quietly does something other than the work it stands in for --
# which is how `memdog-reconcile` once re-embedded with a stale model and
# concluded nothing was stale.


async def evaluate_gap(
    pool: asyncpg.Pool, alert_id: str, *, trigger: str, record: bool = True,
    from_sequence: int | None = None, judge_override=None,
) -> dict:
    """Evaluate everything this alert has not seen, up to `batch_cap`.

    `record=False` is the backtest: same path, same matching, writes nothing and
    delivers nothing, and does not move the watermark. Same discipline as a
    crawler dry run -- what it reports is what a live run would actually do,
    because it *is* the live run with its writes withheld.
    """
    alert = await pool.fetchrow(
        "SELECT * FROM alerts WHERE alert_id = $1 AND deleted_at IS NULL", alert_id)
    if alert is None:
        raise AlertError("alert not found", status=404)

    if record and alert["overlap"] == "skip":
        live = await pool.fetchval(
            "SELECT run_id FROM alert_runs WHERE alert_id = $1 AND status = 'running' LIMIT 1",
            alert_id)
        if live:
            # Recorded as skipped rather than queued: queueing guarantees a
            # backlog that never drains for any alert slower than its arrivals.
            return {"alert_id": alert_id, "status": "skipped", "run_id": None}

    start = alert["watermark"] if from_sequence is None else from_sequence
    cap = alert["batch_cap"]
    where = _loads(alert["where_clause"])

    run_id = new_id("alq")
    await pool.execute(
        """
        INSERT INTO alert_runs (run_id, alert_id, config_version, trigger,
            from_sequence, status)
        VALUES ($1, $2, $3, $4, $5, 'running')
        """,
        run_id, alert_id, alert["config_version"], trigger, start,
    )

    try:
        with span("alert.evaluate", alert_id=alert_id, trigger=trigger):
            # One more than the cap, so "is there more after this" is a fact
            # rather than a guess that a full batch means more.
            rows = await pool.fetch(
                """
                SELECT event_id, sequence, payload, org_id, project_id, data_id,
                       occurred_at
                  FROM domain_events
                 WHERE sequence > $1 AND event_type = $2
                   AND org_id = $3 AND (project_id = $4 OR project_id IS NULL)
                 ORDER BY sequence
                 LIMIT $5
                """,
                start, alert["surface"], alert["org_id"], alert["project_id"], cap + 1,
            )
            deferred = max(0, len(rows) - cap)
            rows = rows[:cap]

            # The selector runs first in both modes. In `llm` mode it is the
            # gate that decides what the model is even shown -- which is why a
            # description without one is refused: it would put every transition
            # in the project in front of a model.
            matched = []
            for row in rows:
                payload = _loads(row["payload"])
                if matches_selector(where, payload):
                    matched.append((row, payload))

            # Scope after the selector and before the model: it is a join, so
            # it is cheaper than a judgement and there is no reason to pay for
            # judging something that was never in scope.
            scope = _loads(alert["scope"])
            if scope and matched:
                keep = await in_scope(pool, scope, matched)
                matched = [m for m in matched if m[0]["sequence"] in keep]

            model_calls = 0
            if alert["mode"] == "llm" and matched:
                from .judging import JudgeUnavailable, build_judge

                judge = judge_override or build_judge(load_settings())
                candidates = [
                    {"key": str(r["sequence"]), **pl} for r, pl in matched
                ]
                try:
                    # One call for the batch. Per candidate would cost what
                    # per-write evaluation was rejected for.
                    verdicts = await judge.judge(
                        alert["describe"], candidates, surface=alert["surface"])
                except JudgeUnavailable as exc:
                    # Deferred, not guessed. The watermark stays put and the
                    # sweep will try again -- the same choice embeddings make,
                    # because a wrong verdict is a false alarm or a silence and
                    # both look like the feature working.
                    raise AlertError(f"judging deferred: {exc}", status=503) from exc
                model_calls = 1
                by_key = {v.key: v for v in verdicts}
                judged = []
                for r, pl in matched:
                    # A candidate the model did not mention is not matched.
                    # Inventing a match from an omission is worse than missing
                    # one, and stalling the batch on it is worse than both.
                    verdict = by_key.get(str(r["sequence"]))
                    if verdict is not None and verdict.matched:
                        judged.append((r, {**pl, "_confidence": verdict.confidence,
                                           "_evidence": verdict.evidence}))
                matched = judged

            # A backtest that reports only a count cannot be judged. "Fourteen"
            # is not calibration -- seeing *which* fourteen is, and it is the
            # only way to tell a selector that works from one that matches
            # everything.
            samples = [
                {"sequence": r["sequence"], "occurred_at": r["occurred_at"],
                 "payload": pl}
                for r, pl in matched[:SAMPLE_LIMIT]
            ]

            if record:
                for row, payload in matched:
                    await pool.execute(
                        """
                        INSERT INTO observed_events (event_id, alert_id, config_version,
                            run_id, org_id, project_id, surface, source_event_id,
                            data_id, fact_id, memory_id, case_id, payload,
                            matched_by, confidence)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                                $13::jsonb, $14, $15)
                        ON CONFLICT (alert_id, source_event_id) DO NOTHING
                        """,
                        new_id("oev"), alert_id, alert["config_version"], run_id,
                        row["org_id"], alert["project_id"], alert["surface"],
                        row["event_id"], row["data_id"], payload.get("fact_id"),
                        payload.get("memory_id"), payload.get("case_id"),
                        json.dumps(payload),
                        "model" if alert["mode"] == "llm" else "selector",
                        payload.get("_confidence"),
                    )
                    # Queued with the record, so "recorded but never queued"
                    # cannot happen. Whether it is *sent* is a separate status:
                    # a subscriber being down must not roll back the record of
                    # what happened.
                    from .event_delivery import enqueue

                    recorded_id = await pool.fetchval(
                        "SELECT event_id FROM observed_events "
                        " WHERE alert_id = $1 AND source_event_id = $2",
                        alert_id, row["event_id"])
                    if recorded_id:
                        await enqueue(pool, recorded_id,
                                      project_id=alert["project_id"], alert_id=alert_id)

            last = rows[-1]["sequence"] if rows else start
            if record:
                # Only now. A watermark advanced before the matches were written
                # steps over transitions nobody ever looked at, and nothing comes
                # back for them -- silent, and permanent.
                await pool.execute(
                    "UPDATE alerts SET watermark = $2 WHERE alert_id = $1", alert_id, last)

            await pool.execute(
                """
                UPDATE alert_runs SET status = 'completed', to_sequence = $2,
                       candidates = $3, matches = $4, deferred = $5,
                       model_calls = $6, finished_at = now()
                 WHERE run_id = $1
                """,
                run_id, last, len(rows), len(matched), deferred, model_calls,
            )
    except Exception as exc:
        await pool.execute(
            "UPDATE alert_runs SET status = 'failed', error = $2, finished_at = now() "
            "WHERE run_id = $1", run_id, str(exc)[:500])
        raise

    return {"alert_id": alert_id, "run_id": run_id, "status": "completed",
            "candidates": len(rows), "matches": len(matched), "deferred": deferred,
            "to_sequence": last, "recorded": record, "samples": samples,
            "sampled": len(samples), "window_from": start, "window_to": last,
            "model_calls": model_calls}


async def backtest(
    pool: asyncpg.Pool, principal: Principal, alert_id: str, since_sequence: int = 0,
) -> dict:
    """Run history through the live path, record nothing, approve the version."""
    principal.require(CONFIG_WRITE)
    await _owned(pool, principal, alert_id)
    result = await evaluate_gap(
        pool, alert_id, trigger="backtest", record=False, from_sequence=since_sequence)
    await pool.execute(
        "UPDATE alerts SET backtested_version = config_version WHERE alert_id = $1",
        alert_id)
    return result


async def tick(pool: asyncpg.Pool, *, limit: int = 20, envelope=None) -> dict:
    """The floor. Evaluates every enabled alert whose watermark is behind.

    Not an optimisation: Cloud Run scales to zero and the queue is in-process,
    so a window in flight dies with its instance. The rows are the record of
    outstanding work and this is what re-derives it.
    """
    head = await pool.fetchval("SELECT coalesce(max(sequence), 0) FROM domain_events")
    behind = await pool.fetch(
        """
        SELECT alert_id FROM alerts
         WHERE enabled AND deleted_at IS NULL AND watermark < $1
         ORDER BY watermark
         LIMIT $2
         FOR UPDATE SKIP LOCKED
        """,
        head, limit,
    )
    evaluated = [await evaluate_gap(pool, r["alert_id"], trigger="tick") for r in behind]

    # Nothing else wakes on `next_attempt_at`. Without this a quiet project
    # retries never, and a dead subscriber's backlog sits forever.
    delivered = None
    if envelope is not None:
        from .event_delivery import deliver_owed

        delivered = await deliver_owed(pool, envelope)
    return {"evaluated": evaluated, "delivered": delivered}


async def poll_events(
    pool: asyncpg.Pool, principal: Principal, *, since: int = 0,
    alert_id: str | None = None, limit: int = 100,
) -> dict:
    """The pull half. Visibility is resolved here, never stored on the event.

    A fact-surface event is visible exactly when its fact is, so the predicate
    comes from the graph rather than from a copy taken when the alert matched --
    which would be stale the moment the subject was re-shared, and would ignore
    a revocation in between.
    """
    principal.require(DATA_READ)
    from .acl import visibility_sql
    from .graph import _fact_visibility

    org_id, user_id, principals = visibility_params(principal)
    # One predicate per kind of subject, OR'd. A fact is visible when its
    # evidence is; an item on its own terms; a memory when you own it or can see
    # something in it -- the same three rules those subjects already use, rather
    # than a fourth invented here.
    item_visible = visibility_sql("di", 1, 2, 3)
    visible = f"""(
        (o.fact_id IS NOT NULL AND {_fact_visibility(1, 2, 3)})
     OR (o.fact_id IS NULL AND o.data_id IS NOT NULL AND {item_visible})
     OR (o.memory_id IS NOT NULL AND (
            m.owner_id = $2
            OR EXISTS (SELECT 1 FROM memory_members mm
                         JOIN data_items d ON d.data_id = mm.data_id
                        WHERE mm.memory_id = m.memory_id
                          AND {visibility_sql("d", 1, 2, 3)})
        ))
    )"""
    rows = await pool.fetch(
        f"""
        SELECT o.event_id, o.sequence, o.alert_id, o.config_version, o.surface,
               o.source_event_id, o.data_id, o.fact_id, o.memory_id, o.case_id,
               o.payload, o.matched_by, o.confidence, o.occurred_at,
               a.name AS alert_name,
               -- Whether anyone was actually told. A feed that shows a match
               -- and hides that its delivery is dead is the half of the story
               -- that matters least.
               (SELECT count(*) FROM event_deliveries d
                 WHERE d.event_id = o.event_id) AS deliveries,
               (SELECT count(*) FROM event_deliveries d
                 WHERE d.event_id = o.event_id AND d.status = 'delivered') AS delivered,
               (SELECT count(*) FROM event_deliveries d
                 WHERE d.event_id = o.event_id AND d.status = 'dead') AS undeliverable
          FROM observed_events o
          JOIN alerts a ON a.alert_id = o.alert_id
          LEFT JOIN entity_facts f ON f.fact_id = o.fact_id
          LEFT JOIN data_items di ON di.data_id = o.data_id
          LEFT JOIN memories m ON m.memory_id = o.memory_id
         WHERE o.sequence > $4 AND o.org_id = $1
           AND ($5::text IS NULL OR o.alert_id = $5)
           AND {visible}
         ORDER BY o.sequence
         LIMIT $6
        """,
        org_id, user_id, principals, since, alert_id, limit,
    )
    out = [dict(r) | {"payload": _loads(r["payload"])} for r in rows]
    return {"events": out, "cursor": out[-1]["sequence"] if out else since}


# -- capture ----------------------------------------------------------------


async def emit_transition(conn, event_type: str, *, org_id: str,
                          project_id: str, payload: dict,
                          data_id: str | None = None) -> None:
    """Record a transition where alerts can read it, in the caller's transaction.

    Synchronous because a transition is observable **only while it happens**:
    once `access_level` reads `org` the previous value is gone, and no later
    sweep recovers it. `domain_events` rather than a table of its own -- it
    already carries the monotonic sequence an alert reads forward from, and a
    second log would be a second place work can be lost.

    Deliberately cheap: one insert, no alert is consulted, and nothing here
    knows whether any alert cares. That is what keeps write latency independent
    of how many alerts a project has.
    """
    from .events import emit

    await emit(conn, event_type=event_type, org_id=org_id, project_id=project_id,
               data_id=data_id, payload=payload)


# -- the async consumer -----------------------------------------------------

ALERT_TOPIC = "alerts"


class AlertWorker:
    """Wakes on a transition, then waits.

    The waiting is the design. A consumer that evaluates one message at a time
    is per-write evaluation with a queue in front of it -- N alerts by M writes,
    and every write still paying for every alert. So a message says only
    *something happened*; the worker coalesces for `debounce_seconds` and then
    evaluates once over whatever accumulated.

    A burst of ten thousand transitions from one crawl is a handful of windows.
    Six revisions of one document inside a window are one candidate.

    Nothing here is the record. The alert watermark in Postgres is, so a message
    lost between publish and handler -- or a window lost with the instance that
    was holding it, which Cloud Run's scale-to-zero makes routine -- costs
    latency rather than an alert. `memdog-alert-tick` picks it up.
    """

    def __init__(self, pool: asyncpg.Pool, *, debounce_seconds: float = 5.0) -> None:
        self._pool = pool
        self._debounce = debounce_seconds
        self._window: object | None = None

    def register(self, queue, topic: str = ALERT_TOPIC) -> None:
        queue.subscribe(topic, self.handle)

    async def handle(self, message) -> None:
        import asyncio

        # The message is a doorbell, not work. Marking it consumed here is
        # honest: the dispatch *was* handled. What remains outstanding is the
        # evaluation, and the alert watermark is what records that -- so the
        # reconciler must not keep re-dispatching a transition forever on the
        # grounds that nothing acknowledged it.
        event_id = getattr(message, "body", message)
        if isinstance(event_id, dict):
            event_id = event_id.get("event_id")
        if event_id:
            from .events import mark_consumed

            await mark_consumed(self._pool, event_id)

        # One window at a time. Every message arriving while one is open is
        # absorbed by it, which is the whole point -- a task per message would
        # be the thing this exists to avoid.
        if self._window is not None and not self._window.done():
            return
        self._window = asyncio.create_task(self._evaluate_after_window())

    async def _evaluate_after_window(self) -> None:
        import asyncio

        await asyncio.sleep(self._debounce)
        try:
            await tick(self._pool)
        except Exception:  # noqa: BLE001
            # The tick will find this work again -- the watermark did not move.
            # Failing loudly here would only take the instance down with it.
            log.exception("alert window failed; the sweep will re-derive it")

    async def drain(self) -> None:
        """Finish an open window. For tests and for a clean shutdown."""
        if self._window is not None and not self._window.done():
            await self._window
