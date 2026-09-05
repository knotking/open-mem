"""The public demo: one corpus, no login, a meter on the tap.

Everything else in this system authenticates first and decides what you may
read second. This surface inverts that -- it has no caller to identify -- so it
cannot borrow any of the machinery that makes the rest of the API safe. What
replaces that is narrowness:

**One project, one memory, named in configuration.** Not "whatever the token
says", not a parameter -- an environment variable set by whoever deployed it. A
request cannot ask for a different corpus, so there is no scope to escalate.

**Read-only, and only the question path.** No write, no entities, no graph
traversal, no memory listing. The public surface is one endpoint that answers
one question over one corpus, and every additional verb here would be another
thing to prove safe.

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

import asyncpg

from .ids import new_id


class DemoUnavailable(Exception):
    """Not an error in the caller's request. Carries a sentence a visitor can
    read, because the alternative -- a bare 429 -- reads as broken."""

    def __init__(self, message: str, status: int = 429) -> None:
        super().__init__(message)
        self.status = status


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
