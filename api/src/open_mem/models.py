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
                                 dimensions, declared_by, licence, hardware, pricing, notes,
                                 hosting)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (model_id) DO UPDATE SET provider = EXCLUDED.provider,
            capabilities = EXCLUDED.capabilities, context_tokens = EXCLUDED.context_tokens,
            dimensions = EXCLUDED.dimensions, declared_by = EXCLUDED.declared_by,
            pricing = EXCLUDED.pricing, notes = EXCLUDED.notes,
            hosting = EXCLUDED.hosting
        """,
        card["model_id"], card.get("provider", "unknown"), card.get("family"),
        card.get("capabilities", []), card.get("context_tokens"), card.get("dimensions"),
        card.get("declared_by", "vendor"), card.get("licence"),
        card.get("hardware", {}), card.get("pricing", {}), card.get("notes"),
        # An operator who does not say gets the answer that costs something
        # rather than the one that leaks something.
        card.get("hosting", "remote"),
    )
    return card


async def candidacy_failure(
    pool: asyncpg.Pool,
    *,
    org_id: str,
    data_type: str,
    model_id: str,
    provider: str | None,
    hosting: str | None,
) -> str | None:
    """Why this model may not serve this data type, or `None` if it may.

    Two rules, both of which were written down long before anything checked
    them, and both of which fail closed:

    **A regulated type is served only by a model hosted inside the deployment.**
    Not "a model from an approved vendor" -- approval is about who you have a
    contract with, and residency is about who receives the bytes. A card that
    does not declare its hosting is treated as remote, so the way to make a
    model a candidate for regulated content is to say where it runs.

    **A provider allow-list, once set, is exhaustive.** The setting register
    describes `allowed_providers` as how "only our approved providers" is
    enforced rather than suggested, and until now it was suggested: nothing read
    it. An empty list still means "no list", because the alternative -- an empty
    list forbidding everything -- would break every deployment that never set
    one, and a control that fires on a default nobody chose is a control people
    switch off.

    Returned as a sentence rather than raised, because the same rules are used
    to *report* on assignments that already exist, where raising would stop at
    the first one.
    """
    from .settings_store import resolve

    profile = await pool.fetchrow(
        "SELECT sensitivity FROM data_type_profiles WHERE data_type = $1", data_type
    )
    sensitivity = profile["sensitivity"] if profile else "standard"

    if sensitivity == "regulated" and (hosting or "remote") != "local":
        return (
            f"{data_type} is {sensitivity} and {model_id} is hosted "
            f"{hosting or 'remote'}; regulated content is served only by a model "
            "that runs inside this deployment"
        )

    allowed = (await resolve(pool, "allowed_providers", org_id=org_id)).value or []
    if allowed and provider and provider not in allowed:
        return (
            f"{provider} is not in this organization's allowed providers "
            f"({', '.join(sorted(allowed))})"
        )
    return None


async def local_models(pool: asyncpg.Pool, model_ids) -> set[str]:
    """Which of these run inside the deployment boundary.

    A model with no card is absent from the result, which is the failing-closed
    direction: an engine nobody described is not evidence that content may be
    sent to it.
    """
    rows = await pool.fetch(
        "SELECT model_id FROM model_cards WHERE model_id = ANY($1::text[]) "
        "AND hosting = 'local'",
        list(model_ids),
    )
    return {r["model_id"] for r in rows}


async def sensitivity_of(pool: asyncpg.Pool, data_type: str | None) -> str:
    if not data_type:
        return "standard"
    return await pool.fetchval(
        "SELECT sensitivity FROM data_type_profiles WHERE data_type = $1", data_type
    ) or "standard"


async def violations(pool: asyncpg.Pool, org_id: str) -> list[dict]:
    """Assignments that exist and would now be refused.

    The rules above are enforced on new assignments only. Retroactively voiding
    what a deployment is already running would take a service down to enforce a
    control it did not know it was breaking -- so the existing pairings are
    reported instead, and `GET /api/v1/models` carries the list. A control that
    is silent about what it would have caught is a control nobody can act on.
    """
    rows = await pool.fetch(
        """
        SELECT a.purpose, a.data_type, a.model_id, c.provider, c.hosting
        FROM model_assignments a
        LEFT JOIN model_cards c ON c.model_id = a.model_id
        WHERE a.org_id = $1
        """,
        org_id,
    )
    found = []
    for row in rows:
        reason = await candidacy_failure(
            pool, org_id=org_id, data_type=row["data_type"],
            model_id=row["model_id"], provider=row["provider"],
            hosting=row["hosting"],
        )
        if reason:
            found.append({
                "purpose": row["purpose"], "data_type": row["data_type"],
                "model_id": row["model_id"], "reason": reason,
            })
    return found


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
        "SELECT capabilities, dimensions, provider, hosting FROM model_cards "
        "WHERE model_id = $1", model_id
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

    # Sensitivity and the provider allow-list. Checked here because the
    # roadmap's rule is that a cloud engine is never a *candidate* for a
    # regulated type -- refusing at inference time would be a runtime failure on
    # content that should never have been routed there in the first place.
    refusal = await candidacy_failure(
        pool,
        org_id=principal.org_id,
        data_type=data_type,
        model_id=model_id,
        provider=card["provider"] if card else None,
        hosting=card["hosting"] if card else None,
    )
    if refusal:
        raise ModelError(refusal, status=409)

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
        resolved = Assignment(purpose, row["data_type"], row["model_id"], row["engine_id"],
                              row["provider"], row["scope"], "assignment")
    else:
        resolved = Assignment(purpose, "*", default_model, None, None, "platform", "default")

    # The second half of the candidacy rule, and the half that matters when an
    # assignment predates it. Reporting a violation is enough for an operator to
    # act on; it is not enough to stop the bytes, and the deployment *default*
    # was never checked by anything -- so a regulated type with no assignment at
    # all would have gone wherever the deployment happened to point.
    #
    # Refusing here means the caller records why it did not interpret the item,
    # which is the existing shape for "we deliberately did not spend on this".
    card = await pool.fetchrow(
        "SELECT provider, hosting FROM model_cards WHERE model_id = $1",
        resolved.model_id,
    )
    refusal = await candidacy_failure(
        pool, org_id=org_id, data_type=data_type or "*",
        model_id=resolved.model_id,
        provider=card["provider"] if card else resolved.provider,
        hosting=card["hosting"] if card else None,
    )
    if refusal:
        raise ModelError(refusal, status=409)
    return resolved


async def list_catalog(pool: asyncpg.Pool, principal: Principal) -> dict:
    cards = await pool.fetch(
        "SELECT model_id, provider, capabilities, context_tokens, dimensions, "
        "declared_by, pricing, notes, hosting FROM model_cards "
        "ORDER BY provider, model_id"
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
        # Assignments that predate the candidacy rules and would now be
        # refused. Reported rather than voided: retroactively invalidating what
        # a deployment is already running takes a service down to enforce a
        # control it did not know it was breaking.
        "violations": await violations(pool, principal.org_id),
        # Never the credential itself, only whether one is held.
        "engines": [dict(e) for e in engines],
    }


# Shipped cards. Static data merged with whatever an operator registers, which
# is the same pattern the provider metadata already uses and is air-gap
# friendly -- a local deployment has a catalog without calling anyone.
SHIPPED_CARDS = [
    {"model_id": "gemini-3.7-flash", "provider": "google", "family": "gemini",
     "capabilities": ["extraction", "classification", "vision", "transcription"],
     "context_tokens": 1_048_576, "declared_by": "vendor", "hosting": "remote",
     "notes": "General multimodal. Fabricates on non-speech audio -- prefer a "
              "purpose-built transcriber for that."},
    {"model_id": "gemini-3.5-transcribe", "provider": "google", "family": "gemini",
     "capabilities": ["transcription"], "context_tokens": 98_304,
     "declared_by": "measured", "hosting": "remote",
     "notes": "Returns empty on non-speech rather than inventing a transcript."},
    {"model_id": "local-hash-v1", "provider": "local", "capabilities": ["embedding"],
     "dimensions": 768, "declared_by": "measured", "hosting": "local",
     "notes": "Deterministic feature hash. Offline and reproducible; not semantic."},
    {"model_id": "local-heuristic-v1", "provider": "local", "capabilities": ["extraction"],
     "declared_by": "measured", "hosting": "local",
     "notes": "Deterministic envelope. Always produces a title, never invents fields."},

    # --- open models, through Ollama -----------------------------------------
    #
    # One provider, several weights. Ollama is the engine; which model it pulls
    # is a string, so these are cards rather than code -- and every one of them
    # resolves to a builder that exists, which is the rule `test_catalog` now
    # enforces.
    #
    # `hosting` is "local" for a self-hosted Ollama and is what makes these the
    # only models a regulated data type may be served by. Pointing an engine's
    # `base_url` at Ollama Cloud makes that untrue, which is why hosting is a
    # property of the *engine* an operator registers as well as of the card.
    #
    # Context windows are the published defaults. Ollama serves a shorter one
    # unless `num_ctx` is raised, so these are the ceiling rather than a promise.
    #
    # None of them declares "answer", because that is not a purpose: chat
    # follows the org's *extraction* assignment on purpose, so that
    # `allowed_providers` is one decision rather than two. Assigning one of
    # these for extraction is what also makes it answer -- which needed
    # `OllamaAnswerer` to exist, and until now silently fell back to the
    # deployment's default answerer instead.
    {"model_id": "llama3.3:70b", "provider": "ollama", "family": "llama",
     "capabilities": ["extraction", "classification"],
     "context_tokens": 131_072, "declared_by": "vendor", "hosting": "local",
     "notes": "Strong general open model. Needs roughly 40GB to serve at Q4."},
    {"model_id": "llama3.2:3b", "provider": "ollama", "family": "llama",
     "capabilities": ["extraction", "classification"],
     "context_tokens": 131_072, "declared_by": "vendor", "hosting": "local",
     "notes": "Small enough for a laptop. Weakest at structured extraction -- "
              "check a sample before assigning it to a data type."},
    {"model_id": "qwen2.5:32b", "provider": "ollama", "family": "qwen",
     "capabilities": ["extraction", "classification"],
     "context_tokens": 131_072, "declared_by": "vendor", "hosting": "local",
     "notes": "Reliable at schema-constrained output, which is what extraction "
              "asks for."},
    {"model_id": "mistral-small:24b", "provider": "ollama", "family": "mistral",
     "capabilities": ["extraction", "classification"],
     "context_tokens": 32_768, "declared_by": "vendor", "hosting": "local",
     "notes": "Shorter context than the others; fine for records, tight for "
              "long transcripts."},
    {"model_id": "gemma3:27b", "provider": "ollama", "family": "gemma",
     "capabilities": ["extraction", "classification"],
     "context_tokens": 131_072, "declared_by": "vendor", "hosting": "local",
     "notes": "Open-weight Gemini lineage."},
    {"model_id": "deepseek-r1:32b", "provider": "ollama", "family": "deepseek",
     "capabilities": ["extraction"],
     "context_tokens": 131_072, "declared_by": "vendor", "hosting": "local",
     "notes": "A reasoning model: slower, and it emits a thinking block the "
              "schema constraint has to survive. Try it on a sample first."},
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
                                     context_tokens, dimensions, declared_by, notes,
                                     hosting)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (model_id) DO NOTHING
            """,
            card["model_id"], card["provider"], card.get("family"), card["capabilities"],
            card.get("context_tokens"), card.get("dimensions"), card["declared_by"],
            card.get("notes"),
            # Not `.get(..., "remote")` by accident: every shipped card states
            # it, and a KeyError here is the right outcome for one that forgot.
            card["hosting"],
        )
    for profile in SHIPPED_PROFILES:
        await pool.execute(
            """
            INSERT INTO data_type_profiles (data_type, requires, sensitivity)
            VALUES ($1, $2, $3) ON CONFLICT (data_type) DO NOTHING
            """,
            profile["data_type"], profile["requires"], profile["sensitivity"],
        )
