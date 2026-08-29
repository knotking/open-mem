"""The queue seam.

The interface is small; the *semantics* differ -- ack deadlines, ordering keys,
redelivery -- which is exactly why it is an interface. The in-process
implementation is not a stub: with one instance there is nothing to distribute,
and it is what lets the local profile drop a broker entirely.

Trace context travels in the message headers so the queue hop does not break the
span, which is the one thing that is genuinely awkward to add later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol


@dataclass
class Message:
    topic: str
    body: dict
    headers: dict[str, str] = field(default_factory=dict)
    attempt: int = 1
    # Times this was put back because the provider was busy rather than
    # because the message was bad. Tracked separately from `attempt` so a
    # quota window cannot exhaust a retry budget meant for real failures.
    deferrals: int = 0


Handler = Callable[[Message], Awaitable[None]]

log = logging.getLogger(__name__)


# Provider capacity, not message fault. Matched by name so the queue does not
# have to import the inference, chat and multimodal layers to know that a
# rate limit is a different kind of problem from a bug.
_CAPACITY_FAILURES = {
    "EmbeddingUnavailable", "AnswerRateLimited", "MultimodalUnavailable",
}


def _is_capacity_failure(exc: BaseException) -> bool:
    return exc.__class__.__name__ in _CAPACITY_FAILURES


class Queue(Protocol):
    async def publish(self, topic: str, body: dict, headers: dict[str, str] | None = None) -> None: ...
    def subscribe(self, topic: str, handler: Handler) -> None: ...
    async def depth(self) -> int: ...


class InProcessQueue:
    """Single-instance implementation. Retries with a bounded backoff.

    Redelivery here is at-least-once, like the brokers it stands in for, so
    handlers written against it must be idempotent -- which is the property the
    contract tests check across all three implementations.
    """

    def __init__(self, *, max_attempts: int = 5, base_delay: float = 0.05) -> None:
        # Deferrals are counted separately from attempts and are not bounded
        # the same way: waiting out a quota window is the correct behaviour,
        # where retrying a genuinely broken message forever is not.
        self._queues: dict[str, asyncio.Queue[Message]] = {}
        self._handlers: dict[str, Handler] = {}
        self._workers: list[asyncio.Task] = []
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._active = 0
        self.dead_letters: list[tuple[Message, str]] = []

    def _queue(self, topic: str) -> asyncio.Queue[Message]:
        return self._queues.setdefault(topic, asyncio.Queue())

    async def publish(self, topic: str, body: dict, headers: dict[str, str] | None = None) -> None:
        from .telemetry import inject_context

        # The trace crosses the hop here. Without it the write and the work it
        # queued are two unrelated traces.
        await self._queue(topic).put(Message(topic, body, inject_context(dict(headers or {}))))

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic] = handler
        self._workers.append(asyncio.create_task(self._run(topic)))

    async def depth(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait until every *subscribed* topic is quiet.

        Deliberately not "until every queue is empty": a message published to a
        topic nobody consumes can never complete, so waiting on it is a hang
        rather than a wait. That distinction is not hypothetical -- it is
        exactly the state an item is in between `searchable` and `enriched` when
        the enrich worker is down.
        """
        async def quiet() -> bool:
            pending = sum(
                self._queues[t].qsize() for t in self._handlers if t in self._queues
            )
            return pending == 0 and self._active == 0

        async def wait() -> None:
            while not await quiet():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait(), timeout)

    async def close(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()

    async def _run(self, topic: str) -> None:
        queue, handler = self._queue(topic), self._handlers[topic]
        while True:
            message = await queue.get()
            self._active += 1
            try:
                await handler(message)
            except Exception as exc:
                if _is_capacity_failure(exc):
                    # A quota or rate limit is not a failing message, it is a
                    # busy provider -- the same message will succeed unchanged
                    # once capacity returns. Counting it against the retry
                    # budget means five quick refusals discard work that was
                    # never faulty, which is how a re-embed silently completes
                    # having embedded almost nothing.
                    #
                    # Backoff still grows, so this is not a spin.
                    message.deferrals += 1
                    await asyncio.sleep(
                        min(self._base_delay * 2 ** message.deferrals, 30.0)
                    )
                    await queue.put(message)
                    continue
                if message.attempt < self._max_attempts:
                    message.attempt += 1
                    await asyncio.sleep(self._base_delay * 2 ** (message.attempt - 1))
                    await queue.put(message)
                    continue
                # Exhausted. The item stays at its current state rather than
                # advancing -- nothing is lost, enrichment is simply behind.
                # Saying so is not optional: a handler that fails every time is
                # indistinguishable from an idle queue if it fails quietly.
                self.dead_letters.append((message, repr(exc)))
                log.error(
                    "dropping message on %s after %d attempts: %r",
                    message.topic, message.attempt, exc,
                )
            finally:
                queue.task_done()
                self._active -= 1
