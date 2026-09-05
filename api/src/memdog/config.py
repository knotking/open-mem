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

    # Bytes go to GCS when a bucket is configured, and to the filesystem
    # otherwise. Cloud Run's filesystem is memory, so the bucket is not optional
    # there -- it is the difference between durable and gone on the next scale-in.
    # Media is the expensive tier by a wide margin, so interpretation is opt-in
    # per deployment and bounded per item. Off means media still stores, with
    # the reason recorded -- nothing is rejected either way.
    media_interpretation: bool = field(
        default_factory=lambda: _env("MEDIA_INTERPRETATION", "false").lower() == "true"
    )
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    multimodal_model: str = field(
        default_factory=lambda: _env("MULTIMODAL_MODEL", "gemini-3.7-flash")
    )
    # Empty means "use multimodal_model". Named separately because
    # transcription and vision are different purposes with different
    # cost-per-quality curves, and the day that matters this is a config change.
    transcribe_model: str = field(default_factory=lambda: _env("TRANSCRIBE_MODEL", ""))
    max_media_bytes: int = field(
        default_factory=lambda: int(_env("MAX_MEDIA_BYTES", str(18 * 1024 * 1024)))
    )

    # Set to enable identity-token sign-in alongside API keys. Absent, the API
    # is key-only -- which is the correct default for a headless deployment.
    firebase_project_id: str = field(
        default_factory=lambda: _env("FIREBASE_PROJECT_ID", "")
    )

    raw_bucket: str = field(default_factory=lambda: _env("RAW_BUCKET", ""))
    blob_root: str = field(default_factory=lambda: _env("BLOB_ROOT", "./.blobs"))

    max_items_per_write: int = field(
        default_factory=lambda: int(_env("MAX_ITEMS_PER_WRITE", "500"))
    )
    max_payload_bytes: int = field(
        default_factory=lambda: int(_env("MAX_PAYLOAD_BYTES", str(32 * 1024 * 1024)))
    )
    # Uploads bypass the base64 inflation of an inline write, so their ceiling
    # is higher -- and separate, because they are a different admission path.
    max_upload_bytes: int = field(
        default_factory=lambda: int(_env("MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
    )
    max_queue_depth: int = field(default_factory=lambda: int(_env("MAX_QUEUE_DEPTH", "10000")))
    # Raw usage rows are one per inference call, so at ingest rates they are
    # millions. They exist to settle a dispute or debug a spike and are only
    # wanted while that window is open; the daily rollup is what reporting reads
    # and is kept. 0 disables the purge for a deployment that ships them
    # elsewhere first.
    # The public demo. Empty `public_project_id` disables the whole surface,
    # which is the correct default: an unauthenticated endpoint that costs money
    # per request must be switched on deliberately, never inherited.
    public_project_id: str = field(
        default_factory=lambda: _env("PUBLIC_PROJECT_ID", ""))
    public_memory_id: str = field(
        default_factory=lambda: _env("PUBLIC_MEMORY_ID", ""))
    public_title: str = field(default_factory=lambda: _env("PUBLIC_TITLE", ""))
    public_subtitle: str = field(default_factory=lambda: _env("PUBLIC_SUBTITLE", ""))
    # Per-IP, per-hour. Low, because the thing being rationed is a model call.
    public_rate_per_hour: int = field(
        default_factory=lambda: int(_env("PUBLIC_RATE_PER_HOUR", "20")))
    # Questions per day across everyone. This is the bill, and it is a hard
    # stop rather than a throttle: the answer past it is "come back tomorrow",
    # not a slower queue, because a queue still spends.
    public_daily_cap: int = field(
        default_factory=lambda: int(_env("PUBLIC_DAILY_CAP", "500")))

    usage_retention_days: int = field(
        default_factory=lambda: int(_env("USAGE_RETENTION_DAYS", "90"))
    )

    chunk_chars: int = field(default_factory=lambda: int(_env("CHUNK_CHARS", "1200")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("CHUNK_OVERLAP", "150")))


def load_settings() -> Settings:
    return Settings()
