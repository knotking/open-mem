"""ULIDs.

A ULID is not a random string: 48 bits of millisecond timestamp followed by 80
bits of randomness, Crockford-base32 encoded. Three properties follow, and they
are the reason for the choice -- ids sort chronologically, creation time is
recoverable without a lookup, and they are generated without coordination.

FR-SCH-12. Note what this does *not* do: `event_time` is never encoded in an id,
because a corrected event_time would otherwise mean a changed primary key.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid(now_ms: int | None = None) -> str:
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, 10) + _encode(rand, 16)


def new_id(prefix: str) -> str:
    """`data_01JQRS...` -- the prefix names the type, the body carries the time."""
    return f"{prefix}_{ulid()}"
