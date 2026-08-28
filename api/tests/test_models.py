"""Model assignment, resolved at runtime.

The evidence that motivated this is in the repo's own history: a general model
asked to transcribe a tone invented a conversation, while a purpose-built
transcriber returned nothing. Sizing every purpose identically is not a neutral
default.
"""

from __future__ import annotations

import pytest

from memdog import models
from memdog.crypto import Envelope
from memdog.models import ModelError

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def catalog(pool):
    await models.ensure_catalog(pool)
    return pool


async def test_most_specific_assignment_wins(catalog, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    await models.assign(catalog, actor, purpose="transcription",
                        model_id="gemini-3.7-flash", data_type="*")
    await models.assign(catalog, actor, purpose="transcription",
                        model_id="gemini-3.5-transcribe", data_type="audio")

    audio = await models.resolve_model(
        catalog, purpose="transcription", data_type="audio",
        org_id=tenant.org_id, default_model="fallback",
    )
    video = await models.resolve_model(
        catalog, purpose="transcription", data_type="video",
        org_id=tenant.org_id, default_model="fallback",
    )
    assert audio.model_id == "gemini-3.5-transcribe"
    assert video.model_id == "gemini-3.7-flash"     # falls back to the wildcard


async def test_the_deployment_default_applies_when_nothing_is_assigned(catalog, tenant):
    """Most deployments will never assign anything, and the shipped
    configuration must work."""
    resolved = await models.resolve_model(
        catalog, purpose="extraction", data_type="document_pdf",
        org_id=tenant.org_id, default_model="local-heuristic-v1",
    )
    assert (resolved.model_id, resolved.source) == ("local-heuristic-v1", "default")


async def test_an_impossible_assignment_fails_when_configured_not_at_3am(
    catalog, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ModelError) as exc:
        await models.assign(catalog, actor, purpose="transcription",
                            model_id="local-hash-v1")
    assert exc.value.status == 409
    assert "does not declare" in str(exc.value)


async def test_a_regulated_data_type_checks_what_the_model_can_do(
    catalog, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ModelError) as exc:
        await models.assign(catalog, actor, purpose="vision",
                            model_id="local-heuristic-v1", data_type="image")
    assert exc.value.status == 409


async def test_embeddings_accept_only_one_vector_space(catalog, tenant, principal_for):
    """A corpus whose vector space depends on what the item happened to be is a
    corpus retrieval cannot reconcile."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ModelError) as exc:
        await models.assign(catalog, actor, purpose="embedding",
                            model_id="local-hash-v1", data_type="document_pdf")
    assert exc.value.status == 409

    ok = await models.assign(catalog, actor, purpose="embedding", model_id="local-hash-v1")
    assert ok.data_type == "*"


async def test_a_provider_credential_is_encrypted_and_never_returned(
    catalog, tenant, principal_for
):
    import base64
    import os

    actor = await principal_for(tenant.api_key)
    envelope = Envelope(os.urandom(32))
    result = await models.register_engine(
        catalog, actor, envelope, provider="google",
        base_url=None, credential="super-secret-key",
    )
    stored = await catalog.fetchval(
        "SELECT credential_ct FROM engines WHERE engine_id = $1", result["engine_id"]
    )
    assert b"super-secret-key" not in bytes(stored)
    assert envelope.decrypt(bytes(stored), aad=tenant.org_id.encode()) == b"super-secret-key"

    listing = await models.list_catalog(catalog, actor)
    engine = next(e for e in listing["engines"] if e["engine_id"] == result["engine_id"])
    assert engine["has_credential"] is True
    assert "credential" not in engine and "credential_ct" not in engine


async def test_credential_storage_fails_closed_without_a_root_key(
    catalog, tenant, principal_for
):
    """Refusing is correct. Storing it in plaintext is not."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ModelError) as exc:
        await models.register_engine(
            catalog, actor, Envelope(None), provider="google",
            base_url=None, credential="secret",
        )
    assert exc.value.status == 503


async def test_the_shipped_catalog_records_what_was_measured(catalog):
    """`declared_by` separates a vendor's claim from something observed, which
    matters when a mapping is derived from it."""
    rows = {r["model_id"]: r for r in await catalog.fetch(
        "SELECT model_id, declared_by, capabilities, notes FROM model_cards"
    )}
    transcriber = rows["gemini-3.5-transcribe"]
    assert transcriber["declared_by"] == "measured"
    assert "inventing" in transcriber["notes"]
    assert rows["gemini-3.7-flash"]["declared_by"] == "vendor"
