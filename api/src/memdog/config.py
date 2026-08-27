"""Settings.

Everything that differs between the three deployment variants is behind an
interface; everything that differs between *installations* is here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://memdog:memdog@localhost:54329/memdog"
        )
    )
    # TBD.md #1: store at the larger dimension. Reducing is a truncation of
    # stored vectors; only increasing costs a corpus-wide re-embed.
    embed_dim: int = field(default_factory=lambda: int(_env("EMBED_DIM", "768")))
    embed_engine: str = field(default_factory=lambda: _env("EMBED_ENGINE", "local"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", ""))
    ollama_url: str = field(
        default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434")
    )
    # Envelope encryption root key, base64. Absent means credential storage
    # fails closed rather than silently storing plaintext.
    master_key_b64: str = field(default_factory=lambda: _env("MEMDOG_MASTER_KEY", ""))

    max_items_per_write: int = field(
        default_factory=lambda: int(_env("MAX_ITEMS_PER_WRITE", "500"))
    )
    max_payload_bytes: int = field(
        default_factory=lambda: int(_env("MAX_PAYLOAD_BYTES", str(32 * 1024 * 1024)))
    )
    max_queue_depth: int = field(default_factory=lambda: int(_env("MAX_QUEUE_DEPTH", "10000")))

    chunk_chars: int = field(default_factory=lambda: int(_env("CHUNK_CHARS", "1200")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("CHUNK_OVERLAP", "150")))


def load_settings() -> Settings:
    return Settings()
