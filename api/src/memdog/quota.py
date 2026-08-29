"""Cost-weighted quota, and the price list it is weighted by.

**Counting requests is the wrong primitive.** A hundred vector searches and a
hundred hybrid-plus-generation requests are the same number to a counter and
three orders of magnitude apart in what they cost. A limiter built on request
count either throttles the cheap calls to protect against the expensive ones, or
lets the expensive ones through to stay usable for the cheap ones. It cannot do
both, because it cannot tell them apart.

So everything here is denominated in **credits**, where one credit is roughly one
plain vector search. Not currency: rating against a rate card needs a
`rate_card_version` to be reproducible and belongs with invoicing. Credits are
the unit that has to exist now, because a budget needs something to decrement
today.

Two mechanisms, and conflating them is the mistake this file exists to avoid:

**The bucket protects the instance.** Per-key, in-process, refilled over time,
charged the *estimated* cost of a request before it runs. It is a burst and
denial-of-service control, and in-process is the correct scope for it -- it
bounds what one caller can make *this* process do. The same reasoning as
`routing.Breaker`: state that would have to be shared to be correct would make
availability depend on the infrastructure it protects.

**The budget protects money.** Durable, daily, decremented by *actual* credits
recorded by the meter, checked before the expensive stage of a request. It is
per-tenant and it is in Postgres, because a per-instance view of spend under
autoscaling is not a view of spend at all -- N instances each enforce the full
cap and the tenant gets N times their budget.

The estimate charges the bucket; the actual decrements the budget. Nothing is
charged twice, and the difference between the two is itself the signal the
dry-run gate needs: an estimate that is never checked against an actual drifts
until nobody trusts it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import asyncpg

# Purpose families. What separates them is the shape of the cost, not the
# feature: generation is priced on output tokens it chose to produce, embedding
# on input tokens it was handed.
GENERATE = "generate"
EMBED = "embed"

PURPOSE_FAMILY: dict[str, str] = {
    "answer": GENERATE,
    "extract": GENERATE,
    "classify": GENERATE,
    "interpret": GENERATE,
    "embed": EMBED,
}

# Credits per thousand tokens, as (input, output, cached).
#
# Output is several times input because providers price it that way, and cached
# input is a fraction because that is the entire point of prompt caching. Two
# rates collapsed into one total would make an agent re-running the same system
# prompt across ten thousand items look identical before and after caching was
# enabled -- which is to say, unimprovable.
TOKEN_PRICE: dict[str, tuple[float, float, float]] = {
    GENERATE: (1.0, 4.0, 0.25),
    # Embeddings are the highest-volume call in the system -- every chunk of
    # every item -- and the cheapest per token. Omitting them entirely, which is
    # the common shortcut, misses the single largest line for bulk ingestion.
    EMBED: (0.1, 0.0, 0.0),
}

# Engines that cost money when they answer.
#
# This is a map rather than a property of the engine because the distinction is
# the base URL, not the name: `ollama` is Ollama Cloud in the hosted variant and
# a local process in the air-gapped one, and they are the same adapter. A
# deployment running its own Ollama should declare it free at the call site
# rather than editing this, since the map is the default and not the authority.
PAID_ENGINES = {"gemini", "ollama"}


def is_paid(engine: str) -> bool:
    return engine in PAID_ENGINES


def credits_for_tokens(
    purpose: str, *, tokens_in: int, tokens_out: int, tokens_cached: int
) -> int:
    """What a completed call cost, in credits.

    Rounded up, and never zero for a call that produced tokens: a thousand calls
    that each round down to nothing are a thousand calls that cost real money
    and metered as free.
    """
    rate_in, rate_out, rate_cached = TOKEN_PRICE.get(
        PURPOSE_FAMILY.get(purpose, GENERATE), TOKEN_PRICE[GENERATE]
    )
    total = (
        tokens_in * rate_in + tokens_out * rate_out + tokens_cached * rate_cached
    ) / 1000.0
    if total <= 0:
        return 0
    return max(1, math.ceil(total))


# --- what a request authorises, before it runs -------------------------------


def estimate_retrieve(*, match: list[str], limit: int) -> int:
    """One credit per search signal, scaled by how many candidates are asked for.

    Merging two ranked lists is cheap next to producing them, so `rrf` adds
    nothing here -- the cost is in the searches, and there is one per signal.
    """
    signals = max(1, len(match))
    return signals * max(1, math.ceil(limit / 20))


# Generation dominates everything above it in the retrieval stack by around
# three orders of magnitude. The estimate is deliberately blunt: it exists to
# stop a retry loop from queueing a thousand generations, and a blunt number
# does that as well as a precise one would.
GENERATION_ESTIMATE = 200


def estimate_ask(*, match: list[str], passages: int) -> int:
    return estimate_retrieve(match=match, limit=passages) + GENERATION_ESTIMATE


def estimate_write(*, items: int, enrich: bool) -> int:
    """A write is cheap unless it asked for enrichment, which is a model call
    per item and is where the cost actually is."""
    return items * (10 if enrich else 1)


# --- the burst bucket --------------------------------------------------------


class QuotaExceeded(Exception):
    """Refused for burst. The caller may retry after the window."""

    def __init__(self, message: str, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.status = 429
        self.retry_after = retry_after


class BudgetExhausted(Exception):
    """Refused for money. Retrying in a moment will not help.

    Named for `queue._CAPACITY_FAILURES`, which matches on class name: a worker
    that hits this must put the message back rather than burn a retry, because
    the message is not faulty and will succeed unchanged when the window rolls.
    """

    def __init__(self, message: str, *, scope: str, retry_after: int = 3600) -> None:
        super().__init__(message)
        self.status = 429
        self.scope = scope
        self.retry_after = retry_after


class Bucket:
    """A token bucket denominated in credits.

    Refills continuously rather than resetting on a boundary, so a caller cannot
    get two full allowances by straddling a minute -- which is the failure mode
    of every fixed-window limiter and the reason the window is not fixed.
    """

    def __init__(self) -> None:
        self._level: dict[str, float] = {}
        self._checked: dict[str, float] = {}

    def charge(self, key: str, cost: int, *, per_minute: int) -> None:
        if per_minute <= 0:          # 0 means unlimited, not "refuse everything"
            return
        now = time.monotonic()
        rate = per_minute / 60.0
        level = self._level.get(key, 0.0)
        last = self._checked.get(key, now)
        level = max(0.0, level - (now - last) * rate)
        if level + cost > per_minute:
            # Seconds until enough has drained for this request specifically,
            # so a large request is not told to retry in a second forever.
            deficit = level + cost - per_minute
            raise QuotaExceeded(
                f"rate limit: {per_minute} credits/minute",
                retry_after=max(1, math.ceil(deficit / rate)),
            )
        self._level[key] = level + cost
        self._checked[key] = now


class Concurrency:
    """In-flight requests per key.

    A bucket bounds arrival rate; it does not bound how many expensive requests
    are running at once. One client with a generous rate limit and slow queries
    can still occupy the whole model tier.
    """

    def __init__(self) -> None:
        self._active: dict[str, int] = {}

    def acquire(self, key: str, *, limit: int) -> None:
        if limit <= 0:
            return
        active = self._active.get(key, 0)
        if active >= limit:
            raise QuotaExceeded(
                f"{limit} concurrent requests already in flight for this credential",
                retry_after=1,
            )
        self._active[key] = active + 1

    def release(self, key: str) -> None:
        active = self._active.get(key, 0) - 1
        if active <= 0:
            self._active.pop(key, None)
        else:
            self._active[key] = active


# --- the durable budget ------------------------------------------------------


@dataclass(frozen=True)
class Spend:
    scope: str
    scope_id: str
    credits: int
    limit: int | None

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.credits >= self.limit


# What binds each scope. A project sits under its org, which sits under the
# platform; a user sits under their org too, not under a project, because a
# person is a member of an organization and works across its projects.
ANCESTRY: dict[str, tuple[str, ...]] = {
    "org": ("platform", "org"),
    "project": ("platform", "org", "project"),
    "user": ("platform", "org", "user"),
}


async def caps(
    pool: asyncpg.Pool,
    *,
    org_id: str | None,
    project_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, int | None]:
    """The daily credit ceiling each scope declares for itself.

    **Deliberately not `settings_store.resolve`.** Precedence answers "which
    value applies", where the most specific wins -- correct for a preference and
    wrong for a ceiling, because it would let the party being limited raise
    their own limit. A cap composes as a minimum down the hierarchy instead, so
    each scope's own value is read here and `_binding_limit` combines them.
    """
    rows = await pool.fetch(
        """
        SELECT scope, value FROM settings
        WHERE key = 'budget_daily_credits'
          AND ((scope = 'platform')
            OR (scope = 'org'     AND scope_id = $1)
            OR (scope = 'project' AND scope_id = $2)
            OR (scope = 'user'    AND scope_id = $3))
        """,
        org_id, project_id, user_id,
    )
    out: dict[str, int | None] = {}
    for row in rows:
        value = row["value"]
        # JSON null is how a scope says "no ceiling here", which differs from
        # having no row at all only in that someone stated it.
        out[row["scope"]] = int(value) if isinstance(value, (int, float)) else None
    return out


def _binding_limit(ceilings: dict[str, int | None], scope: str) -> int | None:
    """The tightest ceiling this scope is subject to.

    A scope is bound by its own value and by every value above it, and the
    smallest of those is what binds. Reading only the scope's own value is how a
    project talks its way past the ceiling its organization set.
    """
    candidates = [
        ceilings[level]
        for level in ANCESTRY.get(scope, (scope,))
        if ceilings.get(level) is not None
    ]
    return min(candidates) if candidates else None


async def _spend(pool: asyncpg.Pool, scope: str, scope_id: str) -> int:
    return int(
        await pool.fetchval(
            """
            SELECT credits FROM usage_spend
            WHERE scope = $1 AND scope_id = $2 AND day = CURRENT_DATE
            """,
            scope, scope_id,
        ) or 0
    )


async def check_budget(
    pool: asyncpg.Pool,
    *,
    org_id: str,
    project_id: str | None = None,
    user_id: str | None = None,
    cost: int = 0,
) -> None:
    """Refuse if a ceiling is already reached. Called before the expensive stage.

    The check is *before* generation and reranking rather than at the front of
    the request, because retrieval is not where the money is and refusing a
    cheap search to protect an expensive one throttles the wrong thing.

    Every scope binds independently, so exhausting a project budget refuses even
    with org headroom to spare -- and the refusal names the scope, because "your
    project is out" and "your organisation is out" have different people to talk
    to.
    """
    ceilings = await caps(pool, org_id=org_id, project_id=project_id,
                          user_id=user_id)
    if not any(v is not None for v in ceilings.values()):
        return

    for scope, scope_id in (("org", org_id), ("project", project_id),
                            ("user", user_id)):
        if not scope_id:
            continue
        limit = _binding_limit(ceilings, scope)
        if limit is None:
            continue
        spent = await _spend(pool, scope, scope_id)
        # At least one credit of headroom is required even when the caller
        # cannot name a cost yet -- which is the usual case, since this runs
        # *before* the model call that determines it. Asking for `> limit` with
        # a cost of zero let a scope that had spent its budget exactly to the
        # last credit keep starting work forever, and disagreed with
        # `Spend.exhausted`, which the reporting surface uses.
        if spent + max(cost, 1) > limit:
            raise BudgetExhausted(
                f"daily budget for this {scope} is spent ({spent}/{limit} credits)",
                scope=scope,
            )


async def spend_today(
    pool: asyncpg.Pool,
    *,
    org_id: str,
    project_id: str | None = None,
    user_id: str | None = None,
) -> list[Spend]:
    """What has been spent against what, for the reporting surface.

    A control nobody can see is a control nobody trusts, and the first question
    after a refusal is always "spent on what, by whom".
    """
    ceilings = await caps(pool, org_id=org_id, project_id=project_id,
                          user_id=user_id)
    return [
        Spend(scope, scope_id, await _spend(pool, scope, scope_id),
              _binding_limit(ceilings, scope))
        for scope, scope_id in (("org", org_id), ("project", project_id),
                                ("user", user_id))
        if scope_id
    ]


# --- the limits themselves ---------------------------------------------------


class Limiter:
    """Bucket, concurrency and the settings lookup that sizes them.

    The settings are cached for a few seconds. A limiter that made a database
    round trip per request to discover its own limit would add latency to every
    call in order to notice a policy change slightly sooner, and the thing it is
    protecting against is a caller sending thousands of requests a second.
    """

    CACHE_SECONDS = 5.0

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._bucket = Bucket()
        self._concurrency = Concurrency()
        self._cache: dict[str, tuple[float, dict]] = {}

    async def _limits(self, org_id: str | None, project_id: str | None) -> dict:
        from .settings_store import resolve

        cache_key = f"{org_id}:{project_id}"
        hit = self._cache.get(cache_key)
        now = time.monotonic()
        if hit and now - hit[0] < self.CACHE_SECONDS:
            return hit[1]
        limits = {}
        for key, default in (
            ("rate_limit_credits_per_minute", 6000),
            ("max_concurrent_requests", 8),
        ):
            resolved = await resolve(
                self._pool, key, org_id=org_id, project_id=project_id
            )
            value = resolved.value
            limits[key] = int(value) if isinstance(value, (int, float)) else default
        self._cache[cache_key] = (now, limits)
        return limits

    async def charge(self, principal, cost: int) -> None:
        """Charge the estimate against the caller's bucket.

        Keyed on the credential rather than the user: two keys held by one
        person are two clients, and a runaway retry loop is a property of a
        client. An unauthenticated producer is keyed on its producer id for the
        same reason.
        """
        limits = await self._limits(principal.org_id, principal.project_id)
        self._bucket.charge(
            _key(principal), cost,
            per_minute=limits["rate_limit_credits_per_minute"],
        )

    async def charge_key(self, key: str, cost: int) -> None:
        """Charge a caller that has no principal.

        The webhook surface, where the only identity a request carries before
        its signature is verified is the producer it names -- and where an
        unbounded caller is the expected case rather than a misused credential.
        Limits resolve at platform scope, since the org is not known yet.
        """
        limits = await self._limits(None, None)
        self._bucket.charge(
            key, cost, per_minute=limits["rate_limit_credits_per_minute"]
        )

    async def hold(self, principal):
        """Occupy a concurrency slot, and hand back the thing that releases it.

        The slot is taken *here* rather than on entering the returned context
        manager, so a refusal raises from this call. Deferring it to `__enter__`
        put the raise outside the caller's `except QuotaExceeded`, which turned
        a 429 into a 500 -- the failure mode being guarded against reported as
        a bug in the guard.
        """
        limits = await self._limits(principal.org_id, principal.project_id)
        key = _key(principal)
        self._concurrency.acquire(key, limit=limits["max_concurrent_requests"])
        return _Slot(self._concurrency, key)


def _key(principal) -> str:
    return getattr(principal, "key_id", None) or getattr(principal, "user_id", None) or "anonymous"


class _Slot:
    """Releases the slot `Limiter.hold` already took."""

    def __init__(self, concurrency: Concurrency, key: str) -> None:
        self._concurrency = concurrency
        self._key = key

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._concurrency.release(self._key)
        return False
