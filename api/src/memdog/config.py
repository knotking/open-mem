"""Settings.

Everything that differs between the three deployment variants is behind an
interface; everything that differs between *installations* is here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _database_url() -> str:
    """One DSN, or the parts.

    A secret manager injects one value per secret, so a managed deployment has
    the password on its own and everything else in plain configuration. Joining
    them here keeps the alternative -- baking a password into a connection
    string that shows up in `describe` output -- from being the easy path.
    """
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    host = os.environ.get("DB_HOST")
    if not host:
        return "postgresql://memdog:memdog@localhost:54329/memdog"
    user = quote(os.environ.get("DB_USER", "postgres"), safe="")
    password = quote(os.environ.get("DB_PASSWORD", ""), safe="")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "memdog")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    # TBD.md #1: store at the larger dimension. Reducing is a truncation of
    # stored vectors; only increasing costs a corpus-wide re-embed.
    embed_dim: int = field(default_factory=lambda: int(_env("EMBED_DIM", "768")))
    embed_engine: str = field(default_factory=lambda: _env("EMBED_ENGINE", "local"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", ""))
    extract_engine: str = field(default_factory=lambda: _env("EXTRACT_ENGINE", "local"))
    extract_model: str = field(default_factory=lambda: _env("EXTRACT_MODEL", ""))
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
