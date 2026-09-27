"""Which engine serves an org, resolved rather than booted.

Every engine already sat behind a Protocol and every one was still chosen once,
from an environment variable — so the seams were polymorphic and the selection
was not. `model_assignments` existed to let an org choose and was read by the
multimodal path alone.

What is worth testing is not that a registry returns something. It is that an
org's assignment reaches the work, that an org which assigned nothing is
completely unaffected, and that the safety rules still judge the model which
would actually run rather than the one the process happens to hold.
"""

from __future__ import annotations

import pytest

from open_mem import models
from open_mem.auth import ApiKeyVerifier
from open_mem.crypto import Envelope
from open_mem.engines import EngineRegistry
from open_mem.extraction import LocalHeuristicExtractor, build_extractor
from open_mem.models import ModelError
from open_mem.settings_store import put

pytestmark = pytest.mark.asyncio


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


def _registry(settings, *, extractor=None, answerer=None):
    from open_mem.chat import ExtractiveAnswerer

    return EngineRegistry(
        settings,
        Envelope.from_settings(settings),
        extractor=extractor or LocalHeuristicExtractor(),
        answerer=answerer or ExtractiveAnswerer(),
    )


async def _engine(pool, settings, tenant, *, provider="ollama",
                  base_url="http://ollama.test"):
    """A registered provider with no credential.

    None of these tests reach a network, so the credential is the one part not
    exercised here — `register_engine` refuses to store one without a root key
    anyway, which is its own test.
    """
    registered = await models.register_engine(
        pool, await _principal(pool, tenant), Envelope.from_settings(settings),
        provider=provider, base_url=base_url, credential=None,
    )
    return registered["engine_id"]


async def _card(pool, model_id, provider, capabilities=("extraction",)):
    await pool.execute(
        """
        INSERT INTO model_cards (model_id, provider, capabilities, declared_by, hosting)
        VALUES ($1, $2, $3, 'operator', 'local')
        ON CONFLICT (model_id) DO UPDATE SET provider = EXCLUDED.provider
        """,
        model_id, provider, list(capabilities),
    )


async def test_an_org_that_assigned_nothing_gets_the_deployment_default(
    pool, settings, tenant
):
    """The common case, and not a degraded one. Most installations will never
    assign anything and the shipped configuration has to work."""
    registry = _registry(settings)
    resolved = await registry.extractor_for(
        pool, org_id=tenant.org_id, data_type="email"
    )
    assert resolved is registry._default_extractor


async def test_an_assignment_reaches_the_work(pool, settings, tenant):
    """The whole point. Before this, an org could assign a model and the
    extraction path would carry on using whatever the process booted with."""
    await models.ensure_catalog(pool)
    await _card(pool, "qwen3-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    await models.assign(
        pool, await _principal(pool, tenant),
        purpose="extraction", model_id="qwen3-local", data_type="*",
        engine_id=engine_id,
    )

    registry = _registry(settings)
    resolved = await registry.extractor_for(
        pool, org_id=tenant.org_id, data_type="email"
    )
    assert resolved is not registry._default_extractor
    assert resolved.model_id == "qwen3-local"


async def test_one_orgs_assignment_does_not_reach_another(
    pool, settings, tenant, other_tenant
):
    await models.ensure_catalog(pool)
    await _card(pool, "qwen3-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    await models.assign(
        pool, await _principal(pool, tenant),
        purpose="extraction", model_id="qwen3-local", data_type="*",
        engine_id=engine_id,
    )

    registry = _registry(settings)
    mine = await registry.extractor_for(pool, org_id=tenant.org_id, data_type="*")
    theirs = await registry.extractor_for(
        pool, org_id=other_tenant.org_id, data_type="*"
    )
    assert mine.model_id == "qwen3-local"
    assert theirs is registry._default_extractor


async def test_a_more_specific_data_type_wins(pool, settings, tenant):
    """Assignment is per `(purpose, data_type)` because the purposes have
    different cost-per-quality curves — and so do the types."""
    await models.ensure_catalog(pool)
    await _card(pool, "general-local", "ollama")
    await _card(pool, "clinical-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    actor = await _principal(pool, tenant)
    await models.assign(pool, actor, purpose="extraction", model_id="general-local",
                        data_type="*", engine_id=engine_id)
    await models.assign(pool, actor, purpose="extraction", model_id="clinical-local",
                        data_type="clinical_note", engine_id=engine_id)

    registry = _registry(settings)
    assert (await registry.extractor_for(
        pool, org_id=tenant.org_id, data_type="clinical_note")).model_id == "clinical-local"
    assert (await registry.extractor_for(
        pool, org_id=tenant.org_id, data_type="email")).model_id == "general-local"


async def test_a_disabled_engine_falls_back_rather_than_failing(pool, settings, tenant):
    """An engine an operator turned off is not an outage. The deployment
    default still answers, and the org's own choice simply stops applying."""
    await models.ensure_catalog(pool)
    await _card(pool, "qwen3-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    await models.assign(
        pool, await _principal(pool, tenant), purpose="extraction",
        model_id="qwen3-local", data_type="*", engine_id=engine_id,
    )
    await pool.execute("UPDATE engines SET enabled = false WHERE engine_id = $1",
                       engine_id)

    registry = _registry(settings)
    resolved = await registry.extractor_for(pool, org_id=tenant.org_id, data_type="*")
    assert resolved is registry._default_extractor


async def test_an_unknown_provider_falls_back_and_says_so(pool, settings, tenant, caplog):
    """There is no implementation for every provider somebody might register.
    Returning the default is right; doing it silently is not."""
    await models.ensure_catalog(pool)
    await _card(pool, "mystery-1", "not-a-provider")
    engine_id = await _engine(pool, settings, tenant, provider="not-a-provider")
    await models.assign(
        pool, await _principal(pool, tenant), purpose="extraction",
        model_id="mystery-1", data_type="*", engine_id=engine_id,
    )

    registry = _registry(settings)
    with caplog.at_level("WARNING"):
        resolved = await registry.extractor_for(pool, org_id=tenant.org_id, data_type="*")
    assert resolved is registry._default_extractor
    assert "not-a-provider" in caplog.text


async def test_candidacy_refuses_before_the_engine_is_built(pool, settings, tenant):
    """The rules have to judge the model that would actually run.

    An org assigning a remotely-hosted model to a regulated type must be
    refused, and refused *loudly* — falling back to the deployment default here
    would route the very content the rule exists to protect.
    """
    await models.ensure_catalog(pool)
    await pool.execute(
        """
        INSERT INTO model_cards (model_id, provider, capabilities, declared_by, hosting)
        VALUES ('cloud-extract', 'gemini', ARRAY['extraction'], 'operator', 'remote')
        """
    )
    await pool.execute(
        """
        INSERT INTO data_type_profiles (data_type, requires, sensitivity)
        VALUES ('clinical_note', ARRAY['extraction'], 'standard')
        ON CONFLICT (data_type) DO UPDATE SET sensitivity = 'standard'
        """
    )
    engine_id = await _engine(pool, settings, tenant, provider="gemini")
    await models.assign(
        pool, await _principal(pool, tenant), purpose="extraction",
        model_id="cloud-extract", data_type="clinical_note", engine_id=engine_id,
    )
    # Marked regulated *after* the assignment, which is how a real violation
    # comes about.
    await pool.execute(
        "UPDATE data_type_profiles SET sensitivity = 'regulated' "
        "WHERE data_type = 'clinical_note'"
    )

    registry = _registry(settings)
    with pytest.raises(ModelError) as exc:
        await registry.extractor_for(
            pool, org_id=tenant.org_id, data_type="clinical_note"
        )
    assert "regulated" in str(exc.value)


async def test_the_provider_allow_list_still_binds(pool, settings, tenant):
    await models.ensure_catalog(pool)
    await _card(pool, "qwen3-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    await models.assign(
        pool, await _principal(pool, tenant), purpose="extraction",
        model_id="qwen3-local", data_type="*", engine_id=engine_id,
    )
    await put(pool, "allowed_providers", ["google"], scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)

    registry = _registry(settings)
    with pytest.raises(ModelError) as exc:
        await registry.extractor_for(pool, org_id=tenant.org_id, data_type="*")
    assert "allowed providers" in str(exc.value)


async def test_engines_are_cached_by_identity_not_by_org(pool, settings, tenant,
                                                         other_tenant):
    """Two orgs on the same model through the same engine share one client. The
    key is what determines behaviour, so it can never hand back a client built
    from somebody else's credential."""
    await models.ensure_catalog(pool)
    await _card(pool, "shared-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    for who in (tenant, other_tenant):
        await pool.execute(
            "INSERT INTO model_assignments (assignment_id, org_id, purpose, data_type,"
            " scope, engine_id, model_id) VALUES ($1,$2,'extraction','*','org',$3,$4)",
            f"asg_{who.org_id[-6:]}", who.org_id, engine_id, "shared-local",
        )

    registry = _registry(settings)
    first = await registry.extractor_for(pool, org_id=tenant.org_id, data_type="*")
    second = await registry.extractor_for(pool, org_id=other_tenant.org_id, data_type="*")
    assert first is second


async def test_embedding_is_not_resolvable_per_org(pool, settings, tenant):
    """Deliberately absent.

    Two orgs extracting with different models produce artifacts that each record
    which model made them, and that is recoverable. Two orgs *embedding* with
    different models write vectors from different spaces into one index, and the
    only signal is that ranking quietly gets worse.
    """
    registry = _registry(settings)
    assert not hasattr(registry, "embedder_for")
    # And the assignment layer already refuses the narrower form that would
    # make it per-type.
    await models.ensure_catalog(pool)
    with pytest.raises(ModelError) as exc:
        await models.assign(
            pool, await _principal(pool, tenant), purpose="embedding",
            model_id="local-hash-v1", data_type="email",
        )
    assert "one vector space" in str(exc.value)


async def test_an_assigned_extractor_gets_its_own_generator_version(
    pool, settings, tenant, blobs
):
    """Artifacts join `generators` for staleness, so an org's assigned model
    needs its own row — or the artifact claims to have come from the
    deployment's model and the reconciler believes it."""
    from open_mem.workers import EnrichWorker

    await models.ensure_catalog(pool)
    await _card(pool, "qwen3-local", "ollama")
    engine_id = await _engine(pool, settings, tenant)
    await models.assign(
        pool, await _principal(pool, tenant), purpose="extraction",
        model_id="qwen3-local", data_type="*", engine_id=engine_id,
    )

    worker = EnrichWorker(pool, build_extractor(settings), settings)
    await worker.ensure_generator()
    worker.attach_registry(_registry(settings, extractor=worker._extractor))

    extractor, generator, refusal = await worker._permitted_extractor(
        "email", tenant.org_id
    )
    assert refusal is None
    assert extractor.model_id == "qwen3-local"
    assert generator != worker.generator_version
    assert await pool.fetchval(
        "SELECT model_id FROM generators WHERE generator_version = $1", generator
    ) == "qwen3-local"
