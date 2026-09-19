"""The public demo: one corpus, no login, a meter on the tap.

Everything else in this system authenticates first and decides what you may
read second. This surface inverts that -- it has no caller to identify -- so it
cannot borrow any of the machinery that makes the rest of the API safe. What
replaces that is narrowness:

**One project, one memory, named in configuration.** Not "whatever the token
says", not a parameter -- an environment variable set by whoever deployed it. A
request cannot ask for a different corpus, so there is no scope to escalate.

**Read-only, and only two shapes of read.** No write, no entity lookup, no
memory listing, and no traversal a caller can steer. The surface is a question
answered over one corpus, and -- since the gallery gained a corpus whose point
is the *shape* of what was extracted -- that corpus's graph, returned whole.

The graph read is the addition that had to earn its place, because the original
rule here was "no graph" and it is worth saying exactly what replaced it. It
takes no traversal parameters at all: no root, no depth, no predicate list, no
clock. A caller names a demo key and receives that project's claims, capped, and
that is the entire vocabulary. The ACL is the traversal's own rather than a
weaker one -- `graph.overview` asks the same visibility predicate of the
evidence and again of every endpoint -- and no record id, record text or corpus
count leaves with it. What comes back is nodes, predicates, and how many times
the corpus asserted each, which is what a picture needs and nothing else.

**Answers are never stored.** The rest of the platform records queries and
optionally their text, attributed to the user who asked. There is no user here,
so the row would be a pile of anonymous questions attached to a religious text
and nothing would ever read it.

**Off unless configured.** An empty `public_project_id` disables the whole
thing. An unauthenticated endpoint that costs money per request must be
switched on deliberately and never inherited from a default.

The meter is in `public_asks`, and the daily cap is a hard stop rather than a
throttle: a throttle still spends, and "the bill grew slowly overnight" is not a
better outcome than "the demo said come back tomorrow".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import asyncpg

from .ids import new_id

log = logging.getLogger(__name__)


class DemoUnavailable(Exception):
    """Not an error in the caller's request. Carries a sentence a visitor can
    read, because the alternative -- a bare 429 -- reads as broken."""

    def __init__(self, message: str, status: int = 429) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ registry

@dataclass(frozen=True)
class Demo:
    """One published corpus, and what it is for.

    `blurb` is the whole reason a gallery beats a single corpus: twelve
    demonstrations of "chat over documents" teach one thing twelve times, so an
    entry that cannot say what it shows that the others do not is an entry that
    should not be in the list.
    """

    key: str
    title: str
    blurb: str
    project_id: str
    memory_id: str
    questions: tuple[str, ...] = ()
    # Where the corpus came from, and that it is synthetic where it is. Carried
    # per demo rather than written into the page: the attribution for a public
    # domain text is not the disclosure a generated legal matter needs, and a
    # component that hardcodes one of them is wrong for every other corpus.
    note: str = ""


def registry(settings) -> dict[str, Demo]:
    """Every corpus this deployment has published, keyed.

    **A list in configuration, resolved server-side.** The narrowness that makes
    this surface safe was originally "one project, named in an environment
    variable, never a parameter". A gallery keeps the same property in its true
    form: a request names a *key*, and a key that is not on this list resolves to
    nothing. What a caller may reach is still decided entirely by whoever
    deployed it.

    The single-corpus configuration keeps working and becomes a one-entry
    registry, so a deployment set up before this existed is unaffected.
    """
    entries: dict[str, Demo] = {}

    raw = (settings.public_demos or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            # Refused loudly rather than silently serving an empty gallery: a
            # typo here is a demo section that vanishes, which reads as a
            # deployment failure and is a configuration one.
            log.error("PUBLIC_DEMOS is not valid JSON, so no gallery: %s", exc)
            parsed = []
        for entry in parsed if isinstance(parsed, list) else []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "").strip()
            project_id = str(entry.get("project_id") or "").strip()
            if not key or not project_id:
                # Both are load-bearing. A key with no project cannot answer and
                # a project with no key cannot be asked for.
                log.error("skipping a PUBLIC_DEMOS entry with no key or project")
                continue
            questions = entry.get("questions") or []
            entries[key] = Demo(
                key=key,
                title=str(entry.get("title") or key),
                blurb=str(entry.get("blurb") or ""),
                project_id=project_id,
                memory_id=str(entry.get("memory_id") or ""),
                questions=tuple(str(q) for q in questions if str(q).strip()),
                note=str(entry.get("note") or ""),
            )

    if not entries and settings.public_project_id:
        entries["default"] = Demo(
            key="default",
            title=settings.public_title or "Ask the corpus",
            blurb=settings.public_subtitle or "",
            project_id=settings.public_project_id,
            memory_id=settings.public_memory_id or "",
        )
    return entries


def resolve(settings, key: str | None) -> Demo:
    """The corpus for this request, or a refusal.

    An absent key takes the first entry, so a caller written against the
    single-corpus endpoint keeps working. An unknown key is a 404 and not a
    lookup against anything -- the point of the registry is that a name the
    deployment did not publish reaches nothing at all.
    """
    published = registry(settings)
    if not published:
        raise DemoUnavailable("no public demo is configured", status=404)
    if not key:
        return next(iter(published.values()))
    demo = published.get(key)
    if demo is None:
        raise DemoUnavailable(f"no demo named {key!r}", status=404)
    return demo


# ------------------------------------------------------------------- metering

def hash_ip(ip: str, secret: str) -> str:
    """Salted with the deployment secret, so the table is not reversible by
    anyone who obtains only the rows.

    Rate limiting needs to know that two requests came from the same place. It
    does not need to know where that is, and a list of addresses paired with the
    questions people asked is a liability nobody asked for.
    """
    return hashlib.sha256(f"{secret}:{ip}".encode()).hexdigest()[:32]


async def check_and_count(
    pool: asyncpg.Pool, *, ip: str, secret: str, question: str,
    rate_per_hour: int, daily_cap: int,
) -> str:
    """Both limits, then the reservation, in one transaction.

    Counting *before* the model call rather than after is deliberate. A crash or
    a timeout between the two would otherwise give away a free call, and the
    failure mode of a public endpoint is precisely a flood of requests that
    error -- which is the moment the meter matters most.
    """
    ip_hash = hash_ip(ip, secret)
    async with pool.acquire() as conn, conn.transaction():
        # The global cap first: when the day is spent it is spent for everyone,
        # and telling one visitor they are rate-limited when the real answer is
        # that the demo is closed sends them away to try again in a minute.
        spent = await conn.fetchval(
            "SELECT count(*) FROM public_asks "
            " WHERE answered AND asked_at > date_trunc('day', now())"
        )
        if spent >= daily_cap:
            raise DemoUnavailable(
                "The public demo has answered its questions for today. It runs "
                "on a fixed daily budget so it stays free — please come back "
                "tomorrow, or sign in to ask against your own data."
            )

        recent = await conn.fetchval(
            "SELECT count(*) FROM public_asks "
            " WHERE ip_hash = $1 AND asked_at > now() - interval '1 hour'",
            ip_hash,
        )
        if recent >= rate_per_hour:
            raise DemoUnavailable(
                f"That is {rate_per_hour} questions in an hour from here, which "
                "is the limit for the public demo. It will reset shortly."
            )

        ask_id = new_id("pask")
        await conn.execute(
            "INSERT INTO public_asks (ask_id, ip_hash, question) VALUES ($1,$2,$3)",
            # Truncated: the meter needs enough to recognise abuse, not a
            # transcript of what anonymous people asked a religious text.
            ask_id, ip_hash, (question or "")[:200],
        )
        return ask_id


async def release(pool: asyncpg.Pool, ask_id: str) -> None:
    """Mark a reservation as not having reached the model.

    A refusal costs nothing and must not count against the daily cap -- without
    this, a burst of failing requests would close the demo for everybody.
    """
    await pool.execute(
        "UPDATE public_asks SET answered = false WHERE ask_id = $1", ask_id
    )


# ------------------------------------------------------------------ caching

# How long a demo's graph is served from memory. A seeded corpus does not change
# between seeds, so the only thing this can serve stale is a re-seed, and that
# corrects itself within the window.
#
# Cached because the graph read is the one *unmetered* thing on this surface.
# `public_asks` exists to bound model spend and a graph query calls no model, so
# metering it would be the wrong instrument -- but an unauthenticated endpoint
# still needs a bound on what a refresh loop can make the database do, and for
# an answer that cannot change between seeds the honest bound is to compute it
# once.
GRAPH_TTL_SECONDS = 300

_graph_cache: dict[str, tuple[float, dict]] = {}


def cached_graph(key: str, now: float) -> dict | None:
    hit = _graph_cache.get(key)
    if hit is None or now - hit[0] > GRAPH_TTL_SECONDS:
        return None
    return hit[1]


def cache_graph(key: str, now: float, payload: dict) -> None:
    # Bounded by the registry: the key comes from `resolve`, which refuses
    # anything the deployment did not publish, so a caller cannot grow this.
    _graph_cache[key] = (now, payload)


# --------------------------------------------------- the shared-answer cache

# How long a stored answer is served before it is asked again. Long enough that
# a link doing the rounds for a week costs one call; short enough that a corpus
# reseeded with better extraction stops serving its old answers within days.
ANSWER_TTL_SECONDS = 7 * 24 * 3600


def question_key(question: str) -> str:
    """The cache key for a question.

    A link that has been through a chat client, an email and a paste differs
    from the original by whitespace and case and nothing else, and those must
    not each buy their own model call. Normalisation is deliberately shallow --
    case, surrounding space and runs of internal whitespace. It stops well short
    of stemming or stripping punctuation, because "what leads to ruin" and "what
    leads to ruin?" are the same question but "is it better" and "is it bettor"
    are not, and a key clever enough to merge the second pair would serve the
    wrong answer with no way for a reader to tell.
    """
    return " ".join(question.lower().split())


async def cached_answer(pool, demo_key: str, question: str) -> dict | None:
    """A stored answer for this corpus and question, or None.

    Counts the hit on the way past. That number is the only evidence the cache
    is doing its job: a table of answers with every row at zero hits is a
    feature that costs a write and saves nothing, and the difference is
    invisible without it.
    """
    row = await pool.fetchrow(
        "UPDATE public_answers SET hits = hits + 1"
        " WHERE demo_key = $1 AND question_key = $2"
        "   AND answered_at > now() - ($3 || ' seconds')::interval"
        " RETURNING payload",
        demo_key, question_key(question), str(ANSWER_TTL_SECONDS),
    )
    if row is None:
        return None
    payload = row["payload"]
    return json.loads(payload) if isinstance(payload, str) else dict(payload)


async def store_answer(pool, demo_key: str, question: str, payload: dict) -> None:
    """Keep an answer for the next person to follow the same link.

    Upsert rather than insert: an entry past its TTL is not deleted on read, so
    the row is still there when the question is asked again and the write has to
    replace it. `answered_at` resets, which is what restarts the clock.
    """
    await pool.execute(
        "INSERT INTO public_answers (demo_key, question_key, question, payload)"
        " VALUES ($1, $2, $3, $4::jsonb)"
        " ON CONFLICT (demo_key, question_key) DO UPDATE"
        "   SET payload = EXCLUDED.payload, answered_at = now(), hits = 0",
        demo_key, question_key(question), question, json.dumps(payload),
    )


def client_ip(request) -> str:
    """The caller's address behind Cloud Run's proxy.

    `request.client.host` is the load balancer, identical for every visitor, so
    using it would make the per-IP limit a global one. `X-Forwarded-For` is
    client-controlled and its *first* entry is therefore a lie anyone can tell;
    the last entry is the one the proxy appended and is the only one worth
    trusting.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return getattr(getattr(request, "client", None), "host", "") or "unknown"
