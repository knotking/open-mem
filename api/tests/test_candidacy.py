"""Sensitivity-driven candidacy, and the provider allow-list.

Both rules were written down long before anything checked them.
`data_type_profiles.sensitivity` was selected beside `requires` and dropped on
the floor; `allowed_providers` appeared in the settings register described as
"enforced rather than suggested" and was read by nothing at all.

The assertions worth reading are the ones about the *default* direction: a card
that says nothing about where it runs is treated as remote, and an org that has
set no allow-list is not thereby forbidden everything.
"""

from __future__ import annotations

import pytest

from open_mem import models
from open_mem.auth import ApiKeyVerifier
from open_mem.models import ModelError
from open_mem.settings_store import put

pytestmark = pytest.mark.asyncio


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _card(pool, model_id, *, provider="acme-ai", hosting=None,
                capabilities=("vision", "extraction")):
    card = {"model_id": model_id, "provider": provider,
            "capabilities": list(capabilities)}
    if hosting is not None:
        card["hosting"] = hosting
    await pool.execute(
        """
        INSERT INTO model_cards (model_id, provider, capabilities, declared_by, hosting)
        VALUES ($1, $2, $3, 'operator', $4)
        ON CONFLICT (model_id) DO UPDATE SET hosting = EXCLUDED.hosting,
            provider = EXCLUDED.provider, capabilities = EXCLUDED.capabilities
        """,
        model_id, provider, list(capabilities), hosting or "remote",
    )


async def _profile(pool, data_type, sensitivity, requires=()):
    await pool.execute(
        """
        INSERT INTO data_type_profiles (data_type, requires, sensitivity)
        VALUES ($1, $2, $3)
        ON CONFLICT (data_type) DO UPDATE SET sensitivity = EXCLUDED.sensitivity,
            requires = EXCLUDED.requires
        """,
        data_type, list(requires), sensitivity,
    )


async def test_a_regulated_type_refuses_a_remotely_hosted_model(pool, tenant):
    """The rule the catalog's own comment stated and nothing enforced."""
    await models.ensure_catalog(pool)
    await _card(pool, "cloud-vision", hosting="remote")
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    with pytest.raises(ModelError) as exc:
        await models.assign(
            pool, await _principal(pool, tenant),
            purpose="vision", model_id="cloud-vision", data_type="clinical_note",
        )
    assert exc.value.status == 409
    assert "regulated" in str(exc.value)
    assert "inside this deployment" in str(exc.value)


async def test_a_regulated_type_accepts_a_locally_hosted_model(pool, tenant):
    await models.ensure_catalog(pool)
    await _card(pool, "medgemma-local", hosting="local")
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    assigned = await models.assign(
        pool, await _principal(pool, tenant),
        purpose="vision", model_id="medgemma-local", data_type="clinical_note",
    )
    assert assigned.model_id == "medgemma-local"


async def test_a_card_that_says_nothing_is_treated_as_remote(pool, tenant):
    """Failing closed. An operator who did not think about hosting registered a
    model that might send content to a third party, and the assumption that
    costs something beats the one that leaks something."""
    await models.ensure_catalog(pool)
    await pool.execute(
        """
        INSERT INTO model_cards (model_id, provider, capabilities, declared_by)
        VALUES ('undeclared', 'someone', ARRAY['vision'], 'operator')
        """
    )
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    hosting = await pool.fetchval(
        "SELECT hosting FROM model_cards WHERE model_id = 'undeclared'"
    )
    assert hosting == "remote"

    with pytest.raises(ModelError):
        await models.assign(
            pool, await _principal(pool, tenant),
            purpose="vision", model_id="undeclared", data_type="clinical_note",
        )


async def test_a_restricted_type_is_not_held_to_the_residency_rule(pool, tenant):
    """`restricted` and `regulated` are different words for a reason. Only the
    stronger one forces local inference."""
    await models.ensure_catalog(pool)
    await _card(pool, "cloud-transcribe", hosting="remote",
                capabilities=["transcription"])
    await _profile(pool, "transcript", "restricted", requires=["transcription"])

    assigned = await models.assign(
        pool, await _principal(pool, tenant),
        purpose="transcription", model_id="cloud-transcribe", data_type="transcript",
    )
    assert assigned.model_id == "cloud-transcribe"


async def test_the_provider_allow_list_is_exhaustive_once_set(pool, tenant):
    await models.ensure_catalog(pool)
    await _card(pool, "outsider", provider="unapproved-inc", hosting="remote")
    await put(pool, "allowed_providers", ["google", "local"], scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)

    with pytest.raises(ModelError) as exc:
        await models.assign(
            pool, await _principal(pool, tenant),
            purpose="vision", model_id="outsider", data_type="image",
        )
    assert "unapproved-inc" in str(exc.value)
    assert "allowed providers" in str(exc.value)


async def test_an_empty_allow_list_forbids_nothing(pool, tenant):
    """An empty list means "no list". The alternative — empty forbids
    everything — breaks every deployment that never set one, and a control that
    fires on a default nobody chose is a control people switch off."""
    await models.ensure_catalog(pool)
    await _card(pool, "anyone", provider="whoever", hosting="remote")

    assigned = await models.assign(
        pool, await _principal(pool, tenant),
        purpose="vision", model_id="anyone", data_type="image",
    )
    assert assigned.model_id == "anyone"


async def test_the_allow_list_admits_a_provider_on_it(pool, tenant):
    await models.ensure_catalog(pool)
    await _card(pool, "approved", provider="google", hosting="remote")
    await put(pool, "allowed_providers", ["google"], scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)

    assigned = await models.assign(
        pool, await _principal(pool, tenant),
        purpose="vision", model_id="approved", data_type="image",
    )
    assert assigned.model_id == "approved"


async def test_an_org_cannot_be_held_to_another_orgs_allow_list(
    pool, tenant, other_tenant
):
    await models.ensure_catalog(pool)
    await _card(pool, "shared-card", provider="whoever", hosting="remote")
    await put(pool, "allowed_providers", ["google"], scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)

    # The neighbour set nothing and is unaffected.
    assigned = await models.assign(
        pool, await _principal(pool, other_tenant),
        purpose="vision", model_id="shared-card", data_type="image",
    )
    assert assigned.model_id == "shared-card"


async def test_existing_assignments_are_reported_rather_than_voided(pool, tenant):
    """Retroactively invalidating what a deployment already runs takes a service
    down to enforce a control it did not know it was breaking."""
    await models.ensure_catalog(pool)
    await _card(pool, "cloud-vision", hosting="remote")
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])
    actor = await _principal(pool, tenant)

    # Assigned before the type was marked regulated — the ordering that produces
    # a pre-existing violation.
    await _profile(pool, "clinical_note", "standard", requires=["vision"])
    await models.assign(
        pool, actor, purpose="vision", model_id="cloud-vision",
        data_type="clinical_note",
    )
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    found = await models.violations(pool, tenant.org_id)
    assert len(found) == 1
    assert found[0]["model_id"] == "cloud-vision"
    assert found[0]["data_type"] == "clinical_note"
    assert "regulated" in found[0]["reason"]

    # Still in the table: reported, not deleted.
    assert await pool.fetchval(
        "SELECT count(*) FROM model_assignments WHERE org_id = $1", tenant.org_id
    ) == 1

    catalog = await models.list_catalog(pool, actor)
    assert catalog["violations"] == found


async def test_a_clean_deployment_reports_no_violations(pool, tenant):
    await models.ensure_catalog(pool)
    assert await models.violations(pool, tenant.org_id) == []


async def test_resolution_refuses_a_model_the_rules_would_not_have_assigned(
    pool, tenant
):
    """The half that matters when an assignment predates the rules — and when
    there is no assignment at all, since the deployment default was never
    checked by anything."""
    await models.ensure_catalog(pool)
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    with pytest.raises(ModelError) as exc:
        await models.resolve_model(
            pool, purpose="vision", data_type="clinical_note",
            org_id=tenant.org_id, default_model="gemini-3.7-flash",
        )
    assert "regulated" in str(exc.value)


async def test_resolution_is_unaffected_for_an_ordinary_type(pool, tenant):
    await models.ensure_catalog(pool)
    resolved = await models.resolve_model(
        pool, purpose="vision", data_type="image",
        org_id=tenant.org_id, default_model="gemini-3.7-flash",
    )
    assert resolved.model_id == "gemini-3.7-flash"
    assert resolved.source == "default"


async def test_the_shipped_catalog_declares_where_everything_runs(pool):
    """A card without hosting is the failure this column exists to prevent, so
    the shipped ones may not rely on the default."""
    await models.ensure_catalog(pool)
    rows = await pool.fetch("SELECT model_id, provider, hosting FROM model_cards")
    by_id = {r["model_id"]: r for r in rows}

    assert by_id["local-hash-v1"]["hosting"] == "local"
    assert by_id["local-heuristic-v1"]["hosting"] == "local"
    assert by_id["gemini-3.7-flash"]["hosting"] == "remote"

    for card in models.SHIPPED_CARDS:
        assert "hosting" in card, f"{card['model_id']} does not say where it runs"


async def test_hosting_is_not_inferred_from_the_provider_name(pool):
    """Provider is who made the model; hosting is where the bytes go. Ollama is
    the same adapter against a local process and against Ollama Cloud, and a
    rule keyed on the vendor would call both of them safe."""
    await models.ensure_catalog(pool)
    await _card(pool, "ollama-cloud", provider="ollama", hosting="remote")
    await _card(pool, "ollama-local", provider="ollama", hosting="local")
    await _profile(pool, "clinical_note", "regulated", requires=["vision"])

    assert await models.candidacy_failure(
        pool, org_id="org_x", data_type="clinical_note",
        model_id="ollama-cloud", provider="ollama", hosting="remote",
    )
    assert await models.candidacy_failure(
        pool, org_id="org_x", data_type="clinical_note",
        model_id="ollama-local", provider="ollama", hosting="local",
    ) is None


# --- the text path -----------------------------------------------------------


async def test_a_regulated_record_is_not_sent_to_a_remote_extractor(
    pool, blobs, settings, tenant
):
    """The gap the image path did not cover, and the larger one: enrichment
    used the deployment-wide extractor and consulted nothing."""
    from open_mem.extraction import GeminiExtractor, LocalHeuristicExtractor
    from open_mem.routing import Chain, Step
    from open_mem.extraction import ChainedExtractor
    from open_mem.workers import EnrichWorker

    await models.ensure_catalog(pool)
    await _profile(pool, "clinical_note", "regulated", requires=["extraction"])
    await _card(pool, "gemini-3.7-flash", provider="google", hosting="remote",
                capabilities=["extraction"])

    remote = GeminiExtractor("key-not-used", "gemini-3.7-flash")
    floor = LocalHeuristicExtractor()
    chained = ChainedExtractor(Chain("extract", [
        Step(name="gemini", model_id=remote.model_id, call=remote.extract),
        Step(name="local", model_id=floor.model_id, call=floor.extract),
    ]))

    worker = EnrichWorker(pool, chained, settings)
    await worker.ensure_generator()

    permitted, generator, refusal = await worker._permitted_extractor("clinical_note")
    assert refusal is None
    # Narrowed, not refused: the floor is local, so the record still gets an
    # envelope — it simply never reaches the engine that would have read it.
    assert permitted.model_ids == ["local-heuristic-v1"]
    # And it carries its own fingerprint, or a locally-produced envelope would
    # be attributed to the model that was refused.
    assert generator != worker.generator_version


async def test_an_ordinary_record_uses_the_whole_chain(pool, settings, tenant):
    from open_mem.extraction import build_extractor
    from open_mem.workers import EnrichWorker

    await models.ensure_catalog(pool)
    worker = EnrichWorker(pool, build_extractor(settings), settings)
    await worker.ensure_generator()

    permitted, generator, refusal = await worker._permitted_extractor("email")
    assert refusal is None
    assert permitted is worker._extractor
    assert generator == worker.generator_version


async def test_a_regulated_record_with_no_local_engine_is_refused(
    pool, settings, tenant
):
    """Withheld rather than sent. The item stays stored and searchable; it is
    the understanding of it that does not happen."""
    from open_mem.extraction import GeminiExtractor
    from open_mem.workers import EnrichWorker

    await models.ensure_catalog(pool)
    await _profile(pool, "clinical_note", "regulated", requires=["extraction"])
    await _card(pool, "gemini-3.7-flash", provider="google", hosting="remote",
                capabilities=["extraction"])

    worker = EnrichWorker(
        pool, GeminiExtractor("key-not-used", "gemini-3.7-flash"), settings
    )
    await worker.ensure_generator()

    permitted, generator, refusal = await worker._permitted_extractor("clinical_note")
    assert permitted is None
    assert "regulated" in refusal
    assert "inside this deployment" in refusal


async def test_a_chain_restricted_to_nothing_is_a_decision_not_to_run(pool):
    """`None` rather than an empty chain: a chain with no steps is not a
    degraded chain."""
    from open_mem.routing import Chain, Step

    async def call(*_a, **_k):
        return "x"

    chain = Chain("extract", [Step(name="a", model_id="m-a", call=call)])
    assert chain.restricted(lambda s: True) is not None
    assert chain.restricted(lambda s: False) is None


async def test_restriction_does_not_inherit_the_original_breaker(pool):
    """Failures recorded against steps that are no longer in the chain would
    open a circuit on evidence about somebody else."""
    from open_mem.routing import Chain, Step

    async def call(*_a, **_k):
        return "x"

    chain = Chain("extract", [
        Step(name="a", model_id="m-a", call=call),
        Step(name="b", model_id="m-b", call=call),
    ])
    for _ in range(5):
        chain.breaker.record_failure("b")
    assert chain.breaker.is_open("b")

    narrowed = chain.restricted(lambda s: s.model_id == "m-b")
    assert not narrowed.breaker.is_open("b")
