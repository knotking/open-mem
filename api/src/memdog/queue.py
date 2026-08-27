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


Handler = Callable[[Message], Awaitable[None]]

log = logging.getLogger(__name__)


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
        self._queues: dict[str, asyncio.Queue[Message]] = {}
        self._handlers: dict[str, Handler] = {}
        self._workers: list[asyncio.Task] = []
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._inflight = 0
        self.dead_letters: list[tuple[Message, str]] = []
        self._idle = asyncio.Event()
        self._idle.set()

    def _queue(self, topic: str) -> asyncio.Queue[Message]:
        return self._queues.setdefault(topic, asyncio.Queue())

    async def publish(self, topic: str, body: dict, headers: dict[str, str] | None = None) -> None:
        self._idle.clear()
        self._inflight += 1
        await self._queue(topic).put(Message(topic, body, headers or {}))

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic] = handler
        self._workers.append(asyncio.create_task(self._run(topic)))

    async def depth(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    async def drain(self, timeout: float = 10.0) -> None:
        """Test and shutdown affordance: wait until nothing is in flight."""
        await asyncio.wait_for(self._idle.wait(), timeout)

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
            try:
                await handler(message)
            except Exception as exc:
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
            self._inflight -= 1
            if self._inflight == 0:
                self._idle.set()
