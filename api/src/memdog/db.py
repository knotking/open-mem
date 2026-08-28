"""Postgres access and migrations.

pgvector and tsvector are Postgres, not separate systems -- that is the central
bet, and it is why there is one connection pool here and no second datastore.
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg

from .config import Settings

MIGRATIONS = Path(__file__).parent / "migrations"


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb in and out as Python objects, not strings.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def create_pool(settings: Settings, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    # Small pools, lazy init: a hundred serverless instances holding ten
    # connections each is the classic serverless-plus-Postgres failure.
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
    )


async def migrate(pool: asyncpg.Pool, settings: Settings) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = path.stem
            applied = await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE version = $1", version
            )
            if applied:
                continue
            sql = path.read_text().replace("{{EMBED_DIM}}", str(settings.embed_dim))
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )


def vector_literal(values: list[float]) -> str:
    """pgvector's text input format. asyncpg has no native vector codec."""
    return "[" + ",".join(f"{v:.8g}" for v in values) + "]"
