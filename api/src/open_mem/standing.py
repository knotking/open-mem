"""W10 — standing queries: say once what you want to be told about.

Everything else here answers when asked. This is the one primitive that speaks
first, and four published use cases are blocked on it — media monitoring being
*only* this, so shipping it without delivery ships nothing.

Three rules hold the whole design, and each of them is a mistake somebody else
made first:

**Never re-scan.** A query sees each item exactly once, walking forward from a
watermark over `domain_events`. A standing query that re-scanned would be a
scheduled full-table scan that somebody registers a hundred of, and it would
look fine until the hundredth.

**Delivery is a read.** A match handed to a principal who cannot see the item is
a leak through the notification channel — worse than a retrieval bug, because
the payload leaves the system for a URL configured months ago. Visibility is
checked under the *owner's* rights at match time, never rights copied when the
query was registered.

**Time is not a selector.** *"Thirty days before a due date"* is not a predicate
over new writes, because nothing arrives on that day. That is a scheduled sweep
over date facets — the expiry sweeper's shape — and conflating the two is
exactly how this engine would quietly become a scanner.

The matcher is `websearch_to_tsquery`, deliberately not the alert system's
`contains`: quoted phrases, `or` and `-exclusion` are what somebody monitoring a
brand actually types, and it is the lexical engine retrieval already uses, so a
standing query and a search agree about what the words mean.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_audit
from .auth import CONFIG_WRITE, DATA_READ, Principal
from .ids import new_id
from .telemetry import span

log = logging.getLogger(__name__)

# The event that means a record exists. One per write, carrying its `data_id`,
# already ordered by `sequence` -- so the alert system's watermark discipline
# works here unchanged rather than being reinvented.
SOURCE_EVENT = "data.recorded"

DELIVERY_KINDS = ("poll", "memory")

# What makes a query fire. Two mechanisms, one surface.
#
# `arrival` matches new writes and never re-scans -- each record is seen once.
# `date` fires when a record's *date* comes within reach, which cannot be a
# predicate over new writes because nothing arrives on the day a deadline
# approaches. Keeping them apart is what stops the arrival engine quietly
# becoming a scanner; sharing the matches, the feed and the delivery is what
# stops the date rule becoming a second product.
QUERY_KINDS = ("arrival", "date")

# The dates a rule may read. Deliberately closed: `event_time` is the record's
# own time, `ingested_at` is when it arrived -- which is what retention ageing
# actually means -- and anything else is a key the producer put in `metadata`.
#
# Model-extracted `key_dates` are **not** here yet. They exist in the envelope
# as text and nothing normalises them to a date, so a rule over them would be
# comparing strings and would silently match nothing.
DATE_FIELDS = ("event_time", "ingested_at")


class StandingError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _loads(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def validate(
    selector: dict, delivery: dict, *, kind: str = "arrival",
    date_field: str | None = None, offset_days: int | None = None,
    window_days: int = 1,
) -> None:
    """Refuse a query that would match everything or deliver nowhere.

    A selector narrowing nothing is not a standing query, it is a copy of the
    project: every write matches, every write is delivered, and the feed is
    indistinguishable from the corpus. Refused rather than accepted and
    regretted, for the same reason `reprocess` refuses one.
    """
    if kind not in QUERY_KINDS:
        raise StandingError(f"kind must be one of {', '.join(QUERY_KINDS)}")
    if kind == "date":
        if not date_field:
            raise StandingError(
                f"a date rule needs a date_field: {', '.join(DATE_FIELDS)}, or "
                "metadata.<key> for something the producer supplied")
        if date_field not in DATE_FIELDS and not date_field.startswith("metadata."):
            raise StandingError(
                f"unknown date field {date_field!r}; use {', '.join(DATE_FIELDS)} "
                "or metadata.<key>")
        if offset_days is None:
            raise StandingError(
                "a date rule needs offset_days -- how far ahead to look. Negative is "
                "the past, which is what retention ageing is")
        if window_days < 1:
            raise StandingError(
                "window_days must be at least 1: a rule matching a date exactly N days "
                "away to the second fires never")
        # A date rule needs no selector -- the date *is* the selector -- so the
        # narrowing check below does not apply to it.
        if not isinstance(selector, dict):
            raise StandingError("selector must be an object")
        _validate_delivery(delivery)
        return
    if not isinstance(selector, dict):
        raise StandingError("selector must be an object")
    known = {"query", "data_type", "tags", "producer_id"}
    unknown = sorted(set(selector) - known)
    if unknown:
        raise StandingError(
            f"cannot select on {', '.join(unknown)}; available: {', '.join(sorted(known))}")
    if not any(selector.get(k) for k in known):
        raise StandingError(
            "a selector must narrow something -- without one, every write matches and the "
            "feed is a copy of the project")
    if selector.get("tags") is not None and not isinstance(selector["tags"], list):
        raise StandingError("tags must be a list")

    _validate_delivery(delivery)


def _validate_delivery(delivery: dict) -> None:
    kind = delivery.get("kind", "poll")
    if kind not in DELIVERY_KINDS:
        raise StandingError(f"delivery must be one of {', '.join(DELIVERY_KINDS)}")
    if kind == "memory" and not delivery.get("memory_key"):
        raise StandingError("delivery to a memory needs a memory_key")


async def create(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, name: str,
    selector: dict, delivery: dict | None = None, kind: str = "arrival",
    date_field: str | None = None, offset_days: int | None = None,
    window_days: int = 1,
) -> dict:
    """Created disabled, like a crawler. Enabling requires a backtest."""
    principal.require(CONFIG_WRITE)
    delivery = delivery or {"kind": "poll"}
    validate(selector, delivery, kind=kind, date_field=date_field,
             offset_days=offset_days, window_days=window_days)

    query_id = new_id("stq")
    # From here, not from the beginning of the corpus. A query registered today
    # is a statement about what arrives next -- starting it at zero would replay
    # the entire history into a feed on its first tick, which is the re-scan
    # this design exists to avoid, wearing a different hat.
    head = await pool.fetchval("SELECT coalesce(max(sequence), 0) FROM domain_events")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO standing_queries (query_id, org_id, project_id, owner_id, name,
                selector, delivery, watermark, kind, date_field, offset_days, window_days)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            query_id, principal.org_id, project_id, principal.user_id, name,
            json.dumps(selector), json.dumps(delivery), head,
            kind, date_field, offset_days, window_days,
        )
        await record_audit(
            conn, principal, action="standing_query.created", project_id=project_id,
            target_type="standing_query", target_id=query_id,
            detail={"name": name, "selector": selector, "delivery": delivery},
        )
    return {"query_id": query_id, "name": name, "enabled": False, "kind": kind,
            "watermark": head, "selector": selector, "delivery": delivery,
            "date_field": date_field, "offset_days": offset_days,
            "window_days": window_days}


async def update(
    pool: asyncpg.Pool, principal: Principal, query_id: str, *,
    selector: dict | None = None, delivery: dict | None = None, name: str | None = None,
) -> dict:
    """Editing what matches drops the approval.

    The same rule a crawler's dry run follows: an approval is for the question
    that was asked, and carrying it onto a changed one is how something nobody
    looked at starts delivering.
    """
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, query_id)
    new_selector = selector if selector is not None else _loads(row["selector"])
    new_delivery = delivery if delivery is not None else _loads(row["delivery"])
    validate(new_selector, new_delivery, kind=row["kind"], date_field=row["date_field"],
             offset_days=row["offset_days"], window_days=row["window_days"])

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE standing_queries
               SET selector = $2, delivery = $3, name = coalesce($4, name),
                   config_version = config_version + 1, updated_at = now()
             WHERE query_id = $1
            """,
            query_id, json.dumps(new_selector), json.dumps(new_delivery), name,
        )
        await record_audit(
            conn, principal, action="standing_query.updated", project_id=row["project_id"],
            target_type="standing_query", target_id=query_id,
            detail={"selector": new_selector, "delivery": new_delivery},
        )
    return await get(pool, principal, query_id)


async def set_enabled(
    pool: asyncpg.Pool, principal: Principal, query_id: str, enabled: bool
) -> dict:
    """Enabling is gated on a backtest of *this* version.

    A selector that matches everything looks identical to one that works until
    somebody reads what it caught, and the moment to read that is before it
    starts delivering rather than after.
    """
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, query_id)
    if enabled and row["backtested_version"] != row["config_version"]:
        raise StandingError(
            "backtest this version before enabling it: a selector that matches everything "
            "looks exactly like one that works until you read what it caught",
            status=409,
        )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE standing_queries SET enabled = $2, updated_at = now() WHERE query_id = $1",
            query_id, enabled,
        )
        await record_audit(
            conn, principal, action="standing_query.enabled" if enabled
            else "standing_query.disabled",
            project_id=row["project_id"], target_type="standing_query", target_id=query_id,
            detail={"enabled": enabled},
        )
    return await get(pool, principal, query_id)


async def delete(pool: asyncpg.Pool, principal: Principal, query_id: str) -> dict:
    principal.require(CONFIG_WRITE)
    await _owned(pool, principal, query_id)
    await pool.execute(
        "UPDATE standing_queries SET deleted_at = now(), enabled = false WHERE query_id = $1",
        query_id)
    return {"query_id": query_id, "deleted": True}


async def _owned(pool: asyncpg.Pool, principal: Principal, query_id: str):
    row = await pool.fetchrow(
        "SELECT * FROM standing_queries WHERE query_id = $1 AND deleted_at IS NULL", query_id)
    if row is None or row["org_id"] != principal.org_id:
        raise StandingError("standing query not found", status=404)
    return row


async def get(pool: asyncpg.Pool, principal: Principal, query_id: str) -> dict:
    principal.require(DATA_READ)
    row = await _owned(pool, principal, query_id)
    return _public(row)


async def listing(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT q.*,
               (SELECT count(*) FROM standing_matches m WHERE m.query_id = q.query_id)
                 AS matches,
               (SELECT max(matched_at) FROM standing_matches m WHERE m.query_id = q.query_id)
                 AS last_match_at,
               (SELECT max(started_at) FROM standing_runs r WHERE r.query_id = q.query_id)
                 AS last_run_at
          FROM standing_queries q
         WHERE q.project_id = $1 AND q.org_id = $2 AND q.deleted_at IS NULL
         ORDER BY q.created_at DESC
        """,
        project_id, principal.org_id,
    )
    return [_public(r) for r in rows]


def _public(row) -> dict:
    out = {k: row[k] for k in row.keys()}
    out["selector"] = _loads(out.get("selector"))
    out["delivery"] = _loads(out.get("delivery"))
    # The one derived field worth serving rather than making every client
    # recompute: whether this version has been looked at.
    out["approved"] = out.get("backtested_version") == out.get("config_version")
    return out


def _predicate(selector: dict, first_param: int) -> tuple[str, list]:
    """The selector, as SQL over one item's row.

    Composed into the candidate query rather than applied afterwards, for the
    same reason the ACL is: filtering after the fact means fetching rows to
    throw them away, and the batch cap would then count rows nobody wanted.
    """
    clauses, params, n = [], [], first_param
    if selector.get("query"):
        params.append(selector["query"])
        # `websearch_to_tsquery` rather than `plainto_`: quoted phrases, `or`
        # and `-exclusion` are what somebody monitoring a brand actually types,
        # and it never raises on syntax the way `to_tsquery` does -- a feed that
        # dies on a stray quote is worse than one that reads it literally.
        clauses.append(
            f"to_tsvector('english', coalesce(d.indexable_text, '')) "
            f"@@ websearch_to_tsquery('english', ${n})")
        n += 1
    if selector.get("data_type"):
        params.append(selector["data_type"])
        clauses.append(f"d.data_type = ${n}")
        n += 1
    if selector.get("tags"):
        params.append(list(selector["tags"]))
        # Overlap, not containment: "any of these" is the question people ask,
        # and `@>` would quietly return nothing whenever more than one was given.
        clauses.append(f"d.tags && ${n}::text[]")
        n += 1
    if selector.get("producer_id"):
        params.append(selector["producer_id"])
        clauses.append(f"d.producer_id = ${n}")
        n += 1
    return " AND ".join(clauses), params


async def evaluate(
    pool: asyncpg.Pool, query_id: str, *, trigger: str, record: bool = True,
    from_sequence: int | None = None,
) -> dict:
    """Everything this query has not seen, up to `batch_cap`.

    `record=False` is the backtest: the same path with its writes withheld and
    the watermark left alone, so what it reports is what a live pass would do
    because it *is* the live pass. It returns the matches themselves — a count
    cannot distinguish a selector that works from one that caught the corpus.
    """
    query = await pool.fetchrow(
        "SELECT * FROM standing_queries WHERE query_id = $1 AND deleted_at IS NULL", query_id)
    if query is None:
        raise StandingError("standing query not found", status=404)

    selector = _loads(query["selector"])
    start = query["watermark"] if from_sequence is None else from_sequence
    cap = query["batch_cap"]

    run_id = new_id("stq_run")
    if record:
        await pool.execute(
            """
            INSERT INTO standing_runs (run_id, query_id, config_version, trigger,
                from_sequence, status)
            VALUES ($1, $2, $3, $4, $5, 'running')
            """,
            run_id, query_id, query["config_version"], trigger, start,
        )

    try:
        with span("standing.evaluate", query_id=query_id, trigger=trigger):
            result = await _evaluate_inner(
                pool, query, selector, start=start, cap=cap, run_id=run_id, record=record)
    except Exception as exc:
        if record:
            await pool.execute(
                "UPDATE standing_runs SET status = 'failed', error = $2, finished_at = now() "
                "WHERE run_id = $1", run_id, str(exc)[:500])
        raise

    if record:
        await pool.execute(
            """
            UPDATE standing_runs SET status = 'completed', to_sequence = $2, candidates = $3,
                   matches = $4, withheld = $5, deferred = $6, finished_at = now()
             WHERE run_id = $1
            """,
            run_id, result["to_sequence"], result["candidates"], result["matches"],
            result["withheld"], result["deferred"],
        )
        # Only now, and only on a completed pass. A watermark advanced before
        # the matches were written steps over items nobody looked at, and
        # nothing ever comes back for them.
        await pool.execute(
            "UPDATE standing_queries SET watermark = $2 WHERE query_id = $1",
            query_id, result["to_sequence"])
    else:
        await pool.execute(
            "UPDATE standing_queries SET backtested_version = config_version WHERE query_id = $1",
            query_id)

    return {"query_id": query_id, "run_id": run_id if record else None,
            "mode": "live" if record else "backtest", **result}


async def _evaluate_inner(
    pool, query, selector: dict, *, start: int, cap: int, run_id: str, record: bool,
) -> dict:
    predicate, params = _predicate(selector, 4)

    # One statement: the watermark walk, the selector and the item join. The
    # candidates are bounded by the cap before any of this reaches Python.
    rows = await pool.fetch(
        f"""
        SELECT e.sequence, d.data_id, d.external_id, d.data_type, d.access_level,
               d.owner_id, d.shared_with,
               left(coalesce(d.indexable_text, ''), 160) AS preview
          FROM domain_events e
          JOIN data_items d ON d.data_id = e.data_id
         WHERE e.event_type = $1 AND e.sequence > $2 AND d.project_id = $3
           AND d.deleted_at IS NULL
           {("AND " + predicate) if predicate else ""}
         ORDER BY e.sequence
         LIMIT {cap}
        """,
        SOURCE_EVENT, start, query["project_id"], *params,
    )

    # How far this pass looked, which is not the same as what it matched. The
    # walk consumes every event up to the head or the cap; the matches are the
    # subset that survived the selector, and advancing only to the last *match*
    # would re-read everything between them forever.
    head = await pool.fetchval(
        """
        SELECT coalesce(max(sequence), $2) FROM (
            SELECT e.sequence FROM domain_events e
             WHERE e.event_type = $1 AND e.sequence > $2
             ORDER BY e.sequence LIMIT $3
        ) window_
        """,
        SOURCE_EVENT, start, cap,
    )
    remaining = await pool.fetchval(
        "SELECT count(*) FROM domain_events WHERE event_type = $1 AND sequence > $2",
        SOURCE_EVENT, head,
    )

    # Delivery is a read, so visibility is resolved under the *owner's* rights
    # now rather than rights copied when the query was registered. Resolved once
    # for the batch: it is a property of the owner, not of the item.
    owner = await _owner_principal(pool, query)
    _, user_id, principals = visibility_params(owner)
    visible_ids = set()
    if rows:
        seen = await pool.fetch(
            f"""
            SELECT d.data_id FROM data_items d
             WHERE d.data_id = ANY($4::text[]) AND {visibility_sql("d", 1, 2, 3)}
            """,
            owner.org_id, user_id, principals, [r["data_id"] for r in rows],
        )
        visible_ids = {r["data_id"] for r in seen}

    matches = []
    for row in rows:
        visible = row["data_id"] in visible_ids
        matches.append({
            "data_id": row["data_id"], "external_id": row["external_id"],
            "data_type": row["data_type"], "sequence": row["sequence"],
            "preview": row["preview"], "visible": visible,
        })

    if record and matches:
        await _record_matches(pool, query, run_id, matches)

    return {
        "candidates": len(rows),
        "matches": sum(1 for m in matches if m["visible"]),
        # Counted and reported rather than dropped: a feed that silently omits
        # what it could not deliver is incomplete in a way nobody can explain,
        # and the number is the evidence that the ACL worked rather than that
        # the selector is broken.
        "withheld": sum(1 for m in matches if not m["visible"]),
        "deferred": remaining,
        "to_sequence": head,
        # The matches themselves, because a count cannot tell a selector that
        # works from one that caught everything.
        "samples": matches[:10],
    }


async def evaluate_date(
    pool: asyncpg.Pool, query_id: str, *, trigger: str, record: bool = True,
) -> dict:
    """Fire on records whose date has come within reach.

    The half W10 cannot do. *"Thirty days before a due date"* is not a predicate
    over new writes, because nothing arrives on that day -- the record showed up
    months earlier and the only thing that changed is the calendar. So this
    scans, deliberately, and the honesty is in the bound: a **window** around
    the offset, which is an index range on a date column rather than a walk of
    the corpus.

    Retention ageing is the same rule pointed backwards. *"Older than seven
    years"* is a negative offset, and one column saying so beats two mechanisms
    that would drift apart.

    Idempotent by the same unique key the arrival path uses: an item matches a
    given rule once, so a sweep running daily over an overlapping window does
    not refill the feed with what it already said.
    """
    query = await pool.fetchrow(
        "SELECT * FROM standing_queries WHERE query_id = $1 AND deleted_at IS NULL", query_id)
    if query is None:
        raise StandingError("standing query not found", status=404)
    if query["kind"] != "date":
        raise StandingError("this is not a date rule", status=400)

    selector = _loads(query["selector"])
    field = query["date_field"]
    # Constructed from a closed vocabulary rather than interpolated: a date
    # field is the one selector value that reaches the FROM clause, and a
    # producer-supplied key goes through the jsonb operator rather than into
    # the SQL text.
    if field == "event_time":
        column, params_extra = "d.event_time", []
    elif field == "ingested_at":
        column, params_extra = "d.ingested_at", []
    else:
        column, params_extra = "(d.metadata ->> $5)::timestamptz", [field.split(".", 1)[1]]

    predicate, extra = _predicate(selector, 6 if params_extra else 5)
    run_id = new_id("stq_run")
    if record:
        await pool.execute(
            """
            INSERT INTO standing_runs (run_id, query_id, config_version, trigger,
                from_sequence, status)
            VALUES ($1, $2, $3, $4, 0, 'running')
            """,
            run_id, query_id, query["config_version"], trigger,
        )

    with span("standing.date", query_id=query_id, trigger=trigger):
        rows = await pool.fetch(
            f"""
            SELECT d.data_id, d.external_id, d.data_type, {column} AS due_at,
                   left(coalesce(d.indexable_text, ''), 160) AS preview
              FROM data_items d
             WHERE d.project_id = $1 AND d.deleted_at IS NULL
               AND {column} IS NOT NULL
               -- The window, and the whole reason this is bounded: everything
               -- whose date lands within `window_days` of the offset from now.
               -- Cast because both sides arrive as untyped parameters and
               -- Postgres cannot pick an operator for `unknown - unknown`.
               AND {column} >= now() + make_interval(days => $2::int - $3::int)
               AND {column} <  now() + make_interval(days => $2::int + $3::int)
               {("AND " + predicate) if predicate else ""}
             ORDER BY {column}
             LIMIT $4
            """,
            query["project_id"], query["offset_days"], query["window_days"],
            query["batch_cap"], *params_extra, *extra,
        )

        owner = await _owner_principal(pool, query)
        _, user_id, principals = visibility_params(owner)
        visible_ids = set()
        if rows:
            seen = await pool.fetch(
                f"""
                SELECT d.data_id FROM data_items d
                 WHERE d.data_id = ANY($4::text[]) AND {visibility_sql("d", 1, 2, 3)}
                """,
                owner.org_id, user_id, principals, [r["data_id"] for r in rows],
            )
            visible_ids = {r["data_id"] for r in seen}

        matches = [{
            "data_id": r["data_id"], "external_id": r["external_id"],
            "data_type": r["data_type"], "sequence": 0,
            "preview": r["preview"], "visible": r["data_id"] in visible_ids,
            "due_at": r["due_at"],
        } for r in rows]

        if record and matches:
            await _record_matches(pool, query, run_id, matches)

    result = {
        "candidates": len(rows),
        "matches": sum(1 for m in matches if m["visible"]),
        "withheld": sum(1 for m in matches if not m["visible"]),
        # A capped run is not "nothing else matched". Reported so a window that
        # is too wide is visible as a number rather than as a feed that seems
        # to stop for no reason.
        "deferred": len(rows) if len(rows) == query["batch_cap"] else 0,
        "to_sequence": query["watermark"],
        "samples": matches[:10],
    }
    if record:
        await pool.execute(
            """
            UPDATE standing_runs SET status = 'completed', to_sequence = $2, candidates = $3,
                   matches = $4, withheld = $5, deferred = $6, finished_at = now()
             WHERE run_id = $1
            """,
            run_id, query["watermark"], result["candidates"], result["matches"],
            result["withheld"], result["deferred"],
        )
    else:
        await pool.execute(
            "UPDATE standing_queries SET backtested_version = config_version WHERE query_id = $1",
            query_id)
    return {"query_id": query_id, "run_id": run_id if record else None,
            "mode": "live" if record else "backtest", **result}


async def _owner_principal(pool, query) -> Principal:
    groups = await pool.fetch(
        "SELECT group_id FROM group_members WHERE user_id = $1", query["owner_id"])
    return Principal(
        user_id=query["owner_id"], org_id=query["org_id"],
        capabilities=frozenset({DATA_READ}),
        project_id=query["project_id"], mode="standing",
        groups=frozenset(r["group_id"] for r in groups),
    )


async def _record_matches(pool, query, run_id: str, matches: list[dict]) -> None:
    from .event_delivery import enqueue_match

    delivery = _loads(query["delivery"])
    # What `sequence` means, per kind, and why it is not one thing.
    #
    # For an arrival query it is the domain-event sequence the match was found
    # at -- naturally monotonic, and shared with the watermark. A date rule has
    # no such number: what moved is the calendar, and every match in one sweep
    # would carry the same head. So a date match takes the next position in
    # *this query's own feed*, which is the only property the `since` cursor
    # actually needs: monotonic within the query being polled.
    #
    # Without it every date match landed at sequence 0, `sequence > 0` excluded
    # all of them, and the feed was empty while the matches were recorded and
    # the memory promotion worked -- the failure that looks like nothing at all.
    dated = query["kind"] == "date"
    async with pool.acquire() as conn, conn.transaction():
        base = await conn.fetchval(
            "SELECT coalesce(max(sequence), 0) FROM standing_matches WHERE query_id = $1",
            query["query_id"]) if dated else 0
        for offset, match in enumerate(matches, start=1):
            if dated:
                match["sequence"] = base + offset
            match_id = await conn.fetchval(
                """
                INSERT INTO standing_matches (match_id, query_id, run_id, data_id,
                    sequence, visible)
                VALUES ($1, $2, $3, $4, $5, $6)
                -- An item matching one query twice is a redelivery, not a
                -- second match: a re-crawl of the same record must not fill a
                -- feed with copies of what it already said.
                ON CONFLICT (query_id, data_id) DO NOTHING
                RETURNING match_id
                """,
                new_id("stm"), query["query_id"], run_id, match["data_id"],
                match["sequence"], match["visible"],
            )
            # Queued in the same transaction as the match, so "matched but
            # never queued" cannot happen -- and only for a match the query's
            # own owner could see, because a subscriber cannot be told about
            # something the query itself was not entitled to. The subscription
            # owner's rights are checked again at send time, since they are a
            # different person.
            if match_id is not None and match["visible"]:
                await enqueue_match(
                    conn, match_id, project_id=query["project_id"],
                    query_id=query["query_id"])
        if delivery.get("kind") == "memory":
            await _promote(conn, query, delivery, matches)


async def _promote(conn, query, delivery: dict, matches: list[dict]) -> None:
    """Delivery with no network in it.

    A match adds the item to a named memory: no URL, no secret, no retry, no
    dead letter -- and it composes with everything already built, since a memory
    can be rolled up, compacted, expired and alerted on.

    Only what the owner can see. Promotion is delivery, and delivery is a read.
    """
    from .memories import add_member, upsert_memory

    memory_id = await upsert_memory(
        conn, org_id=query["org_id"], project_id=query["project_id"],
        type_name=delivery.get("memory_type", "default"),
        memory_key=delivery["memory_key"],
        owner_id=query["owner_id"],
        title=delivery.get("title") or f"Matches: {query['name']}",
    )
    for match in matches:
        if match["visible"]:
            await add_member(conn, memory_id, match["data_id"], "routed")


async def matches_for(
    pool: asyncpg.Pool, principal: Principal, query_id: str, *, since: int = 0,
    limit: int = 100,
) -> dict:
    """The feed, from a cursor.

    Ordered by `sequence` rather than time: two items written in the same
    millisecond would otherwise come back in whichever order the planner liked,
    and a poller resuming from a timestamp would skip one of them.

    Visibility is the reader's, resolved now -- never a copy taken when the
    match was recorded. A record shared with somebody yesterday and unshared
    today is not in their feed today.
    """
    principal.require(DATA_READ)
    query = await _owned(pool, principal, query_id)
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT m.match_id, m.data_id, m.sequence, m.matched_at, m.visible,
               d.external_id, d.data_type,
               left(coalesce(d.indexable_text, ''), 240) AS preview
          FROM standing_matches m
          JOIN data_items d ON d.data_id = m.data_id
         WHERE m.query_id = $5 AND m.sequence > $6
           AND {visibility_sql("d", 1, 2, 3)}
         ORDER BY m.sequence DESC
         LIMIT $4
        """,
        org_id, user_id, principals, limit, query_id, since,
    )
    withheld = await pool.fetchval(
        "SELECT count(*) FROM standing_matches WHERE query_id = $1 AND NOT visible", query_id)
    return {
        "query_id": query_id,
        "name": query["name"],
        "matches": [dict(r) for r in rows],
        # Said rather than hidden: "your query matched things you cannot see" is
        # the difference between a quiet feed and a broken one.
        "withheld": withheld,
        "cursor": max((r["sequence"] for r in rows), default=since),
    }


async def tick(pool: asyncpg.Pool, *, limit: int = 20) -> dict:
    """Every enabled query whose watermark is behind, on the sweep that runs.

    The rows are the record of outstanding work, not the queue: Cloud Run scales
    to zero and the in-process queue dies with its instance, so this is what
    re-derives what is owed.
    """
    head = await pool.fetchval(
        "SELECT coalesce(max(sequence), 0) FROM domain_events WHERE event_type = $1",
        SOURCE_EVENT)
    behind = await pool.fetch(
        """
        SELECT query_id FROM standing_queries
         WHERE enabled AND deleted_at IS NULL AND kind = 'arrival' AND watermark < $1
         ORDER BY watermark
         LIMIT $2
         FOR UPDATE SKIP LOCKED
        """,
        head, limit,
    )
    evaluated = []
    for row in behind:
        try:
            evaluated.append(await evaluate(pool, row["query_id"], trigger="tick"))
        except Exception as exc:  # noqa: BLE001 -- one bad query must not stop the sweep
            log.warning("standing query %s failed: %s", row["query_id"], exc)

    # Date rules are not selected by a watermark -- there is no watermark to be
    # behind, because what moved is the calendar rather than the corpus. Every
    # enabled one is evaluated on every pass, which is affordable precisely
    # because each is a bounded range scan and matches are idempotent.
    dated = await pool.fetch(
        """
        SELECT query_id FROM standing_queries
         WHERE enabled AND deleted_at IS NULL AND kind = 'date'
         ORDER BY created_at
         LIMIT $1
         FOR UPDATE SKIP LOCKED
        """,
        limit,
    )
    for row in dated:
        try:
            evaluated.append(await evaluate_date(pool, row["query_id"], trigger="tick"))
        except Exception as exc:  # noqa: BLE001
            log.warning("date rule %s failed: %s", row["query_id"], exc)
    return {"evaluated": evaluated}
