"""Model routing -- the chain, and what falls through it.

This exists because of a failure that actually happened: a free-tier quota
error left chat returning 429 for an hour while a local engine that cannot be
rate limited sat configured and idle. A single assigned model turns every
provider hiccup into an outage.
"""

from __future__ import annotations

import httpx
import pytest

from open_mem.routing import Breaker, Chain, Step, Unavailable, is_transient

pytestmark = pytest.mark.asyncio


def _step(name, *, fails=None, returns=None):
    calls = []

    async def call(*args, **kwargs):
        calls.append(args)
        if fails is not None:
            raise fails
        return returns

    step = Step(name=name, model_id=f"{name}-model", call=call)
    step.calls = calls  # type: ignore[attr-defined]
    return step


async def test_the_primary_answers_and_nothing_else_is_touched():
    second = _step("floor", returns="floor answer")
    chain = Chain("answer", [_step("gemini", returns="primary answer"), second])

    served = await chain.run("q")
    assert served.result == "primary answer"
    assert served.depth == 0
    assert second.calls == [], "a working primary must not cost a second call"


async def test_a_rate_limit_falls_through_to_the_next_engine():
    """The observed outage: quota exhausted on one provider, a healthy engine
    sitting unused behind it."""
    chain = Chain("answer", [
        _step("gemini", fails=httpx.HTTPError("429 quota exceeded")),
        _step("extractive", returns="quoted passages"),
    ])
    served = await chain.run("q")
    assert served.result == "quoted passages"
    assert served.depth == 1
    assert "gemini" in served.errors[0]


async def test_a_bad_answer_does_not_fall_through():
    """A schema failure means the model answered and answered badly. That is a
    prompt problem; a weaker model is less likely to satisfy the same schema,
    so falling through spends money to fail again."""
    floor = _step("floor", returns="never reached")
    chain = Chain("extract", [_step("gemini", fails=ValueError("schema mismatch")), floor])

    with pytest.raises(ValueError):
        await chain.run("q")
    assert floor.calls == []


async def test_the_chain_reports_which_engine_answered():
    """A fallback answer that looks like a primary one cannot be debugged
    later, and cannot be re-derived."""
    chain = Chain("answer", [
        _step("gemini", fails=Unavailable("down")),
        _step("extractive", returns="ok"),
    ])
    served = await chain.run("q")
    assert served.step.name == "extractive"
    assert served.step.model_id == "extractive-model"


async def test_when_every_engine_fails_the_real_reason_survives():
    chain = Chain("answer", [
        _step("a", fails=Unavailable("first reason")),
        _step("b", fails=Unavailable("second reason")),
    ])
    with pytest.raises(Unavailable) as exc:
        await chain.run("q")
    assert "first reason" in str(exc.value) and "second reason" in str(exc.value)


async def test_a_failing_engine_is_skipped_after_repeated_failures():
    """Stop hammering something that is down: every attempt against a dead
    provider is latency the caller pays for nothing."""
    breaker = Breaker(threshold=2, cooldown_seconds=60)
    primary = _step("gemini", fails=Unavailable("down"))
    chain = Chain("answer", [primary, _step("floor", returns="ok")], breaker=breaker)

    for _ in range(3):
        assert (await chain.run("q")).result == "ok"

    # Two failures opened it; the third request never reached the engine.
    assert len(primary.calls) == 2


async def test_the_last_engine_is_tried_even_with_its_breaker_open():
    """The floor must stay reachable. An open breaker on the only remaining
    engine would turn a protection into the outage it exists to prevent."""
    breaker = Breaker(threshold=1, cooldown_seconds=60)
    breaker.record_failure("floor")
    assert breaker.is_open("floor")

    floor = _step("floor", returns="degraded but real")
    chain = Chain("answer", [_step("gemini", fails=Unavailable("down")), floor],
                  breaker=breaker)
    served = await chain.run("q")
    assert served.result == "degraded but real"
    assert len(floor.calls) == 1


async def test_a_recovered_engine_closes_its_breaker():
    breaker = Breaker(threshold=2, cooldown_seconds=60)
    breaker.record_failure("gemini")
    breaker.record_success("gemini")
    breaker.record_failure("gemini")
    assert not breaker.is_open("gemini"), "success must reset the count, not decrement it"


async def test_availability_failures_are_distinguished_from_bad_answers():
    assert is_transient(httpx.HTTPError("boom"))
    assert is_transient(Unavailable("boom"))
    assert is_transient(TimeoutError())
    assert not is_transient(ValueError("schema mismatch"))
    assert not is_transient(KeyError("missing field"))


async def test_a_chain_needs_at_least_one_engine():
    with pytest.raises(ValueError):
        Chain("answer", [])
