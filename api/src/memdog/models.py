"""Model assignment -- per (purpose, data type, scope), resolved at runtime.

Two things this replaces, both of which were wrong in different ways:

**Environment variables.** Changing a model meant a redeploy, and it changed the
model for everything at once. A deployment cannot then run a purpose-built
transcriber for audio and a general model for documents, which is exactly the
split the evidence here calls for.

**One model for all purposes.** Classification is not reasoning. Transcription
is not extraction. Sizing them identically overpays for the cheap cases and
underperforms on the expensive ones.

Resolution is most-specific-wins, and an assignment is **checked against the
model's card** rather than merely stored -- assigning a text-only model to
transcribe audio should fail when it is configured, not when a file arrives.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .audit import record_audit
from .auth import CONFIG_WRITE, Principal
from .crypto import CryptoUnavailable, Envelope
from .ids import new_id

PURPOSES = ("embedding", "extraction", "classification", "transcription", "vision")


class ModelError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Assignment:
    purpose: str
    data_type: str
    model_id: str
    engine_id: str | None
    provider: str | None
    scope: str
    source: str


async def register_engine(
    pool: asyncpg.Pool,
    principal: Principal,
    envelope: Envelope,
    *,
    provider: str,
    base_url: str | None,
    credential: str | None,
) -> dict:
    """Register a provider. The credential is encrypted, and **fails closed**.

    If there is no root key, this refuses rather than storing the credential in
    plaintext -- the whole point of the envelope existing before anything could
    store a secret.
    """
    principal.require(CONFIG_WRITE)
    ciphertext = None
    if credential:
        try:
            ciphertext = envelope.encrypt(
                credential.encode(), aad=principal.org_id.encode()
            )
        except CryptoUnavailable as exc:
            raise ModelError(
                "cannot store a provider credential: encryption is not configured",
                status=503,
            ) from exc

    engine_id = new_id("eng")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO engines (engine_id, org_id, provider, base_url, credential_ct)
            VALUES ($1, $2, $3, $4, $5)
            """,
            engine_id, principal.org_id, provider, base_url, ciphertext,
        )
        await record_audit(
            conn, principal, action="engine.registered", target_type="engine",
            target_id=engine_id,
            # Never the credential, and never a prefix of it.
            detail={"provider": provider, "has_credential": bool(credential)},
        )
    return {"engine_id": engine_id, "provider": provider, "enabled": True}


async def upsert_card(pool: asyncpg.Pool, principal: Principal, card: dict) -> dict:
    principal.require(CONFIG_WRITE)
    await pool.execute(
        """
        INSERT INTO model_cards (model_id, provider, family, capabilities, context_tokens,
                                 dimensions, declared_by, licence, hardware, pricing, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (model_id) DO UPDATE SET provider = EXCLUDED.provider,
            capabilities = EXCLUDED.capabilities, context_tokens = EXCLUDED.context_tokens,
            dimensions = EXCLUDED.dimensions, declared_by = EXCLUDED.declared_by,
            pricing = EXCLUDED.pricing, notes = EXCLUDED.notes
        """,
        card["model_id"], card.get("provider", "unknown"), card.get("family"),
        card.get("capabilities", []), card.get("context_tokens"), card.get("dimensions"),
        card.get("declared_by", "vendor"), card.get("licence"),
        card.get("hardware", {}), card.get("pricing", {}), card.get("notes"),
    )
    return card


async def assign(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    purpose: str,
    model_id: str,
    data_type: str = "*",
    scope: str = "org",
    engine_id: str | None = None,
) -> Assignment:
    """Assign a model, checking the card first.

    An assignment that cannot work should fail here rather than when a file
    arrives at three in the morning.
    """
    principal.require(CONFIG_WRITE)
    if purpose not in PURPOSES:
        raise ModelError(f"purpose must be one of {', '.join(PURPOSES)}")

    card = await pool.fetchrow(
        "SELECT capabilities, dimensions FROM model_cards WHERE model_id = $1", model_id
    )
    if card is not None and card["capabilities"]:
        if purpose not in card["capabilities"]:
            raise ModelError(
                f"{model_id} does not declare the {purpose!r} capability "
                f"(it declares: {', '.join(card['capabilities'])})",
                status=409,
            )

    profile = await pool.fetchrow(
        "SELECT requires, sensitivity FROM data_type_profiles WHERE data_type = $1", data_type
    )
    if profile is not None and card is not None:
        missing = set(profile["requires"]) - set(card["capabilities"])
        if missing:
            raise ModelError(
                f"{data_type} requires {', '.join(sorted(missing))}, which {model_id} does not declare",
                status=409,
            )

    # Embeddings accept `*` only: a corpus with two embedding models per data
    # type is a corpus whose vector space depends on what the item happened to
    # be, which retrieval cannot reconcile.
    if purpose == "embedding" and data_type != "*":
        raise ModelError(
            "embedding assignments accept data_type '*' only -- one vector space per index",
            status=409,
        )

    assignment_id = new_id("asg")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO model_assignments (assignment_id, org_id, purpose, data_type, scope,
                                           engine_id, model_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (org_id, purpose, data_type, scope)
            DO UPDATE SET model_id = EXCLUDED.model_id, engine_id = EXCLUDED.engine_id
            """,
            assignment_id, principal.org_id, purpose, data_type, scope, engine_id, model_id,
        )
        await record_audit(
            conn, principal, action="model.assigned", target_type="model_assignment",
            target_id=f"{purpose}:{data_type}",
            detail={"model_id": model_id, "scope": scope},
        )
    return Assignment(purpose, data_type, model_id, engine_id, None, scope, "assignment")


async def resolve_model(
    pool: asyncpg.Pool,
    *,
    purpose: str,
    data_type: str | None,
    org_id: str,
    default_model: str,
) -> Assignment:
    """Most specific wins: an exact data_type beats `*`, which beats the
    deployment default.

    The default is not a failure case. Most deployments will never assign
    anything, and the shipped configuration should work.
    """
    rows = await pool.fetch(
        """
        SELECT a.data_type, a.model_id, a.engine_id, a.scope, e.provider
        FROM model_assignments a
        LEFT JOIN engines e ON e.engine_id = a.engine_id
        WHERE a.org_id = $1 AND a.purpose = $2 AND a.data_type IN ($3, '*')
        ORDER BY (a.data_type = '*') ASC
        LIMIT 1
        """,
        org_id, purpose, data_type or "*",
    )
    if rows:
        row = rows[0]
        return Assignment(purpose, row["data_type"], row["model_id"], row["engine_id"],
                          row["provider"], row["scope"], "assignment")
    return Assignment(purpose, "*", default_model, None, None, "platform", "default")


async def list_catalog(pool: asyncpg.Pool, principal: Principal) -> dict:
    cards = await pool.fetch(
        "SELECT model_id, provider, capabilities, context_tokens, dimensions, "
        "declared_by, pricing, notes FROM model_cards ORDER BY provider, model_id"
    )
    assignments = await pool.fetch(
        """
        SELECT a.purpose, a.data_type, a.model_id, a.scope, e.provider
        FROM model_assignments a LEFT JOIN engines e ON e.engine_id = a.engine_id
        WHERE a.org_id = $1 ORDER BY a.purpose, a.data_type
        """,
        principal.org_id,
    )
    engines = await pool.fetch(
        """
        SELECT engine_id, provider, base_url, enabled,
               (credential_ct IS NOT NULL) AS has_credential
        FROM engines WHERE org_id = $1 OR org_id IS NULL ORDER BY provider
        """,
        principal.org_id,
    )
    return {
        "models": [dict(c) for c in cards],
        "assignments": [dict(a) for a in assignments],
        # Never the credential itself, only whether one is held.
        "engines": [dict(e) for e in engines],
    }


# Shipped cards. Static data merged with whatever an operator registers, which
# is the same pattern the provider metadata already uses and is air-gap
# friendly -- a local deployment has a catalog without calling anyone.
SHIPPED_CARDS = [
    {"model_id": "gemini-3.7-flash", "provider": "google", "family": "gemini",
     "capabilities": ["extraction", "classification", "vision", "transcription"],
     "context_tokens": 1_048_576, "declared_by": "vendor",
     "notes": "General multimodal. Fabricates on non-speech audio -- prefer a "
              "purpose-built transcriber for that."},
    {"model_id": "gemini-3.5-transcribe", "provider": "google", "family": "gemini",
     "capabilities": ["transcription"], "context_tokens": 98_304,
     "declared_by": "measured",
     "notes": "Returns empty on non-speech rather than inventing a transcript."},
    {"model_id": "local-hash-v1", "provider": "local", "capabilities": ["embedding"],
     "dimensions": 768, "declared_by": "measured",
     "notes": "Deterministic feature hash. Offline and reproducible; not semantic."},
    {"model_id": "local-heuristic-v1", "provider": "local", "capabilities": ["extraction"],
     "declared_by": "measured",
     "notes": "Deterministic envelope. Always produces a title, never invents fields."},
]

SHIPPED_PROFILES = [
    {"data_type": "clinical_note", "requires": ["extraction"], "sensitivity": "regulated"},
    {"data_type": "transcript", "requires": ["transcription"], "sensitivity": "restricted"},
    {"data_type": "image", "requires": ["vision"], "sensitivity": "standard"},
]


async def ensure_catalog(pool: asyncpg.Pool) -> None:
    for card in SHIPPED_CARDS:
        await pool.execute(
            """
            INSERT INTO model_cards (model_id, provider, family, capabilities,
                                     context_tokens, dimensions, declared_by, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (model_id) DO NOTHING
            """,
            card["model_id"], card["provider"], card.get("family"), card["capabilities"],
            card.get("context_tokens"), card.get("dimensions"), card["declared_by"],
            card.get("notes"),
        )
    for profile in SHIPPED_PROFILES:
        await pool.execute(
            """
            INSERT INTO data_type_profiles (data_type, requires, sensitivity)
            VALUES ($1, $2, $3) ON CONFLICT (data_type) DO NOTHING
            """,
            profile["data_type"], profile["requires"], profile["sensitivity"],
        )
