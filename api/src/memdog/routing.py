"""Model routing -- an ordered chain rather than a single engine.

The failure this exists for is not cost, it is availability. A quota error
from one provider took chat down entirely while a perfectly healthy local
engine sat configured and idle. A single assigned model means every provider
hiccup is an outage.

Three rules shape it:

**Only availability failures fall through.** A rate limit, a 5xx or a timeout
means the model never answered, so asking a different one is free of downside.
A schema or parse failure means the model *did* answer and answered badly --
that is a prompt problem, and a weaker model is less likely to satisfy the same
schema, so falling through spends money to fail again. Those stay terminal and
go to the DLQ where they can be seen.

**The chain ends somewhere that cannot fail.** The last step is always a local
engine, so the system degrades to a worse answer rather than to no answer. That
is what turns a provider outage into a quality dip.

**Which engine served is recorded, not inferred.** `fallback_depth` is on the
artifact and on the metric. Running permanently on a fallback looks exactly
like running normally unless something says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from . import usage
from .telemetry import record, span


class Unavailable(RuntimeError):
    """The model did not answer. Try the next one.

    Distinct from a bad answer, which is not a routing problem.
    """


# Exceptions that mean "no answer arrived", whatever raised them. Kept as a
# tuple rather than a base class because these come from three modules that
# should not have to know about routing to be routable.
TRANSIENT: tuple[type[BaseException], ...] = (Unavailable, httpx.HTTPError, TimeoutError)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT):
        return True
    # Raised by the inference and chat layers for provider unavailability;
    # imported lazily so routing does not depend on either.
    return exc.__class__.__name__ in {
        "EmbeddingUnavailable", "AnswerRateLimited", "MultimodalUnavailable",
    }


@dataclass
class Step:
    """One engine in the chain."""

    name: str
    model_id: str
    call: Callable[..., Awaitable[Any]]


@dataclass
class Served:
    result: Any
    depth: int
    step: Step
    # What went wrong on the way here. Empty on a clean primary answer, and the
    # most useful thing in the record when it is not.
    errors: list[str] = field(default_factory=list)


class Breaker:
    """Stop hammering an engine that is failing.

    In-process and deliberately not durable: Cloud Run instances are ephemeral,
    and a breaker that needed shared state would be a correctness dependency on
    the very infrastructure it protects. Losing the state on restart costs one
    wasted attempt, which is the right price.
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, name: str) -> bool:
        until = self._open_until.get(name)
        if until is None:
            return False
        if time.monotonic() >= until:
            # Half-open: let one attempt through rather than waiting for a
            # signal that can only come from an attempt.
            del self._open_until[name]
            self._failures[name] = self.threshold - 1
            return False
        return True

    def record_failure(self, name: str) -> None:
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self.threshold:
            self._open_until[name] = time.monotonic() + self.cooldown

    def record_success(self, name: str) -> None:
        self._failures.pop(name, None)
        self._open_until.pop(name, None)


class Chain:
    """An ordered set of engines for one purpose."""

    def __init__(self, purpose: str, steps: list[Step], breaker: Breaker | None = None) -> None:
        if not steps:
            raise ValueError("a chain needs at least one step")
        self.purpose = purpose
        self.steps = steps
        self.breaker = breaker or Breaker()

    @property
    def model_id(self) -> str:
        """The primary's identity. What the chain is *assigned* as, which is
        not necessarily what answered -- that is `Served.step`."""
        return self.steps[0].model_id

    async def run(self, *args, **kwargs) -> Served:
        with span("inference.chain", purpose=self.purpose, steps=len(self.steps)):
            return await self._run(*args, **kwargs)

    async def _run(self, *args, **kwargs) -> Served:
        errors: list[str] = []
        last: BaseException | None = None

        primary = self.steps[0].name

        for depth, step in enumerate(self.steps):
            if self.breaker.is_open(step.name) and depth < len(self.steps) - 1:
                # Never skipped for the final step: the floor has to stay
                # reachable, or an open breaker becomes the outage.
                errors.append(f"{step.name}: circuit open")
                record("inference_attempts", 1, engine=step.name,
                       purpose=self.purpose, outcome="skipped")
                await usage.skipped(self.purpose, step.name, depth=depth)
                continue
            try:
                # Every attempt is metered, including the ones that fail: a call
                # that generated tokens and then timed out still cost what it
                # generated. The meter is a context manager so there is no exit
                # from this block that forgets to record one.
                async with usage.meter(
                    self.purpose, step.name,
                    model_id=step.model_id,
                    configured_model=self.steps[0].model_id,
                    depth=depth,
                    primary_engine=primary,
                ):
                    result = await step.call(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                if not is_transient(exc):
                    # The model answered badly. That is not something another
                    # model is likely to fix, and retrying costs real money.
                    record("inference_attempts", 1, engine=step.name,
                           purpose=self.purpose, outcome="rejected")
                    raise
                self.breaker.record_failure(step.name)
                errors.append(f"{step.name}: {exc.__class__.__name__}: {exc}"[:200])
                record("inference_attempts", 1, engine=step.name,
                       purpose=self.purpose, outcome="unavailable")
                last = exc
                continue

            self.breaker.record_success(step.name)
            record("inference_attempts", 1, engine=step.name,
                   purpose=self.purpose, outcome="served")
            record("inference_fallback_depth", depth, purpose=self.purpose,
                   engine=step.name)
            return Served(result, depth, step, errors)

        # Every step failed, including the floor. Raising the last cause rather
        # than a synthetic error keeps the actual reason visible.
        raise Unavailable(
            f"every engine for {self.purpose} failed: {'; '.join(errors)}"
        ) from last
