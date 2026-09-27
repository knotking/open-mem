"""The meter.

`telemetry.py` already counts model calls. It is the wrong instrument for this
question: OpenTelemetry counters are in-process, aggregated and droppable under
load, which is correct for "is inference working" and useless for "who spent
this". A number that may be lost cannot be the basis of a bill or a quota.

So every inference call writes a row. The three things that make it a meter
rather than a log:

**Failed calls count.** A call that times out having generated three thousand
tokens consumed three thousand tokens; a retry doubles it and the fallback chain
can triple it. Recording only successes under-reports spend systematically, and
in the direction that produces surprise. `meter()` is a context manager
specifically so the failure path cannot be forgotten -- there is no code path
through an inference call that leaves the block without a row.

**The engine that answered is what is recorded**, not the one that was
configured. The routing chain turns a provider outage into a quality dip, but it
also means a free local call can silently become a paid cloud one. That is a
category change rather than a degradation, so it gets `crossed_to_paid` of its
own instead of being inferred from a non-zero fallback depth.

**Attribution travels in a context variable.** The alternative is threading an
org, a project, a user and a run id through the extractor, the answerer, the
embedder and the multimodal engine -- four layers that have no other reason to
know who is paying, and any one of which forgetting to pass it along produces
unattributed spend rather than an error. The context is set once, at the edge:
in the request handler, or in the worker that picked the message up.

Rating this into currency, rolling it up by hour and reconciling it against
provider invoices are the async metering consumer's job and are not here. What
is here is the record those need to exist, and the daily rollup enforcement
reads so a budget check is one indexed row rather than a scan.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

import asyncpg

from . import quota
from .ids import new_id
from .telemetry import record

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def configure(pool: asyncpg.Pool) -> None:
    """Give the meter somewhere to write. Called once at startup.

    Module-level rather than injected for the same reason `telemetry.record` is:
    the call sites are deep inside provider adapters, and an adapter that had to
    be handed a connection pool in order to be metered would be an adapter whose
    signature encodes billing.
    """
    global _pool
    _pool = pool


def reset() -> None:
    """Drop the pool reference. Tests use this; nothing else should."""
    global _pool
    _pool = None


@dataclass(frozen=True)
class Attribution:
    """Who pays, and what caused the spend."""

    org_id: str
    project_id: str | None = None
    user_id: str | None = None
    # The primitives that quoted an estimate somewhere and therefore owe an
    # actual: a crawl run's dry-run projection, a reprocess job's rebuild
    # preview, a bulk import's admission check.
    run_id: str | None = None
    data_id: str | None = None
    case_id: str | None = None
    # Whose credentials served it. A user's own provider key is their spend to
    # be shown, not ours to silently cap.
    billing_account: str = "platform"


_attribution: ContextVar[Attribution | None] = ContextVar("usage_attribution", default=None)


@contextmanager
def attributed(attribution: Attribution | None = None, **overrides):
    """Establish who the calls inside this block are charged to.

    Nests: a worker sets the org and project once, and the handler for a single
    item adds the `data_id` without having to restate the rest.
    """
    current = _attribution.get()
    if attribution is None:
        if current is None:
            if "org_id" not in overrides:
                raise ValueError("the outermost attribution must name an org")
            attribution = Attribution(**overrides)
        else:
            attribution = replace(current, **overrides)
    elif overrides:
        attribution = replace(attribution, **overrides)
    token = _attribution.set(attribution)
    try:
        yield attribution
    finally:
        _attribution.reset(token)


@dataclass
class _Attempt:
    purpose: str
    engine: str
    model_id: str | None
    configured_model: str | None
    depth: int
    paid: bool
    crossed_to_paid: bool
    started: float
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    detail: dict = field(default_factory=dict)


_attempt: ContextVar[_Attempt | None] = ContextVar("usage_attempt", default=None)


def observe(
    *, tokens_in: int = 0, tokens_out: int = 0, tokens_cached: int = 0, **detail
) -> None:
    """Report what a provider said it consumed.

    Called from inside the adapter, where the response is parsed -- which is the
    only place the split between input, output and cached tokens still exists.
    By the time a result has been turned into an envelope or a `Generated`, that
    detail has been flattened into a total and the cost cannot be computed from
    it.

    Additive, because one logical call can page: an embedder batching a hundred
    chunks makes several HTTP requests and they are one metered call.
    """
    attempt = _attempt.get()
    if attempt is None:
        # Not an error: the local heuristic engines report nothing because they
        # consume nothing, and a provider called outside a metered block is a
        # bug worth counting rather than raising over inside a billing path.
        record("usage_unattributed", 1)
        return
    attempt.tokens_in += tokens_in
    attempt.tokens_out += tokens_out
    attempt.tokens_cached += tokens_cached
    attempt.detail.update(detail)


@asynccontextmanager
async def meter(
    purpose: str,
    engine: str,
    *,
    model_id: str | None = None,
    configured_model: str | None = None,
    depth: int = 0,
    primary_engine: str | None = None,
    paid: bool | None = None,
):
    """Wrap one inference attempt. Records a row whichever way it ends.

    `paid` overrides the engine map, for the deployment whose `ollama` is a local
    process rather than Ollama Cloud -- the difference is a base URL and the
    adapter is the same, so the name cannot carry it.
    """
    is_paid = quota.is_paid(engine) if paid is None else paid
    crossed = bool(
        depth > 0 and is_paid and primary_engine and not quota.is_paid(primary_engine)
    )
    attempt = _Attempt(
        purpose=purpose,
        engine=engine,
        model_id=model_id,
        configured_model=configured_model,
        depth=depth,
        paid=is_paid,
        crossed_to_paid=crossed,
        started=time.monotonic(),
    )
    token = _attempt.set(attempt)
    status = "ok"
    try:
        yield attempt
    except TimeoutError:
        status = "timeout"
        raise
    except BaseException:
        status = "failed"
        raise
    finally:
        _attempt.reset(token)
        await _record(attempt, status)


async def skipped(purpose: str, engine: str, *, depth: int = 0) -> None:
    """An engine the breaker refused to try. Zero tokens, still an event.

    A chain permanently serving from its fallback because the primary's breaker
    never closes looks exactly like a chain with no primary, unless the skips
    are recorded.
    """
    attempt = _Attempt(
        purpose=purpose, engine=engine, model_id=None, configured_model=None,
        depth=depth, paid=False, crossed_to_paid=False, started=time.monotonic(),
    )
    await _record(attempt, "skipped")


async def _record(attempt: _Attempt, status: str) -> None:
    credits = quota.credits_for_tokens(
        attempt.purpose,
        tokens_in=attempt.tokens_in,
        tokens_out=attempt.tokens_out,
        tokens_cached=attempt.tokens_cached,
    )
    latency_ms = int((time.monotonic() - attempt.started) * 1000)

    record("usage_credits", credits, purpose=attempt.purpose,
           engine=attempt.engine, status=status)
    if attempt.crossed_to_paid:
        # Its own counter, not a label on fallback: somebody needs to be able to
        # alert on "we started paying" without also alerting on every fallback.
        record("usage_crossed_to_paid", 1, purpose=attempt.purpose,
               engine=attempt.engine)

    attribution = _attribution.get()
    if _pool is None or attribution is None:
        # A model call with nobody to bill is a real condition -- a CLI smoke
        # test, a unit test with no database -- and not one worth failing the
        # call over. It is counted so it cannot be silent.
        record("usage_unattributed", 1, purpose=attempt.purpose)
        return

    try:
        async with _pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO usage_events (
                    usage_id, org_id, project_id, user_id, run_id, data_id, case_id,
                    purpose, configured_model, serving_engine, serving_model,
                    fallback_depth, crossed_to_paid, billing_account,
                    tokens_in, tokens_out, tokens_cached, credits,
                    latency_ms, status, detail
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                          $15,$16,$17,$18,$19,$20,$21)
                """,
                new_id("use"), attribution.org_id, attribution.project_id,
                attribution.user_id, attribution.run_id, attribution.data_id,
                attribution.case_id, attempt.purpose, attempt.configured_model,
                attempt.engine, attempt.model_id, attempt.depth,
                attempt.crossed_to_paid, attribution.billing_account,
                attempt.tokens_in, attempt.tokens_out, attempt.tokens_cached,
                credits, latency_ms, status, attempt.detail,
            )
            # The rollup moves in the same transaction as the event. An event
            # without its increment would make the budget under-count, which is
            # the one direction a spending control must never fail in.
            for scope, scope_id in (
                ("org", attribution.org_id),
                ("project", attribution.project_id),
                ("user", attribution.user_id),
            ):
                if not scope_id:
                    continue
                await conn.execute(
                    """
                    INSERT INTO usage_spend (scope, scope_id, day, credits, events)
                    VALUES ($1, $2, CURRENT_DATE, $3, 1)
                    ON CONFLICT (scope, scope_id, day) DO UPDATE
                        SET credits = usage_spend.credits + EXCLUDED.credits,
                            events = usage_spend.events + 1,
                            updated_at = now()
                    """,
                    scope, scope_id, credits,
                )
    except Exception as exc:  # noqa: BLE001
        # Metering must not break the operation it measures. Losing a row is a
        # billing defect; failing the user's request because billing failed is a
        # worse one, and the counter above already recorded the call happened.
        record("usage_write_failures", 1, purpose=attempt.purpose)
        log.error("failed to record usage for %s/%s: %r",
                  attempt.purpose, attempt.engine, exc)


async def purge_events(pool: asyncpg.Pool, *, older_than_days: int) -> int:
    """Drop raw events past their retention. The rollup is untouched.

    Two retentions rather than one because the two answer different questions:
    raw events exist to settle a dispute or debug a spike and are only wanted
    while that window is open, and they are the volume. The rollup is what
    reporting reads, it is small, and it is kept.
    """
    result = await pool.execute(
        "DELETE FROM usage_events WHERE occurred_at < now() - ($1 || ' days')::interval",
        str(older_than_days),
    )
    return int(result.split()[-1]) if result else 0
