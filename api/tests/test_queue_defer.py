"""A busy provider is not a broken message.

The failure this fixes was observed during a re-embed: every chunk hit the
embedding provider's rate limit, each burned five retries in a few hundred
milliseconds, and the job reported success having embedded almost nothing. The
corpus was left half in one vector space and half in another, which is the one
state retrieval cannot recover from on its own.
"""

from __future__ import annotations

import pytest

from memdog.queue import InProcessQueue, Message

pytestmark = pytest.mark.asyncio


class Busy(Exception):
    pass


BusyRateLimit = type("EmbeddingUnavailable", (Exception,), {})


async def test_a_rate_limit_does_not_consume_the_retry_budget():
    """Five quick refusals must not discard work that was never faulty."""
    queue = InProcessQueue(max_attempts=3, base_delay=0.001)
    seen = []

    async def handler(message: Message) -> None:
        seen.append(message.attempt)
        if len(seen) < 6:
            raise BusyRateLimit("rate limited")

    queue.subscribe("t", handler)
    await queue.publish("t", {"x": 1})
    await queue.drain()
    await queue.close()

    assert len(seen) == 6, "it kept trying past the retry budget"
    assert queue.dead_letters == [], "and never dead-lettered a message that was fine"
    assert set(seen) == {1}, "a deferral is not an attempt"


async def test_a_genuine_failure_still_exhausts_and_dead_letters():
    """The budget has to keep working, or a permanently broken handler retries
    forever and the queue stops draining."""
    queue = InProcessQueue(max_attempts=3, base_delay=0.001)
    calls = []

    async def handler(message: Message) -> None:
        calls.append(1)
        raise ValueError("genuinely broken")

    queue.subscribe("t", handler)
    await queue.publish("t", {"x": 1})
    await queue.drain()
    await queue.close()

    assert len(calls) == 3
    assert len(queue.dead_letters) == 1
