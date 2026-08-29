"""The demo seed, which is the Phase 1 exit criterion made runnable.

`seed_demo` already asserts its own success and raises `SeedError` naming the
step that broke, so most of this file is one call. What is added here is the
part a manual run cannot give: it happens on every commit, against a fresh
schema, so the exit criterion stops being something someone remembers to check.
"""

from __future__ import annotations

import httpx
import pytest

from memdog import seed as seed_mod
from memdog.seed import SeedError, reset_demo, seed_demo

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def app_client(pool):
    """The real application, mounted in-process.

    Not a fixture path: every request below goes through the actual routing,
    credential check, admission control and write verb. The only thing absent
    compared to an external client is the socket.
    """
    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://seed", timeout=120.0
        ) as client:
            async def drain() -> None:
                await app.state.queue.drain(timeout=300)

            yield app, client, drain


async def test_the_seed_satisfies_the_phase_1_exit_criterion(app_client):
    """Written into a project owned by an org with an access level, found by
    scoped search, cited, audited, and recording which model embedded it."""
    app, client, drain = app_client
    result = await seed_demo(app.state.pool, client, drain=drain)

    assert result.written == 40
    # Three usage exports are written with enrichment off. Recording is cheap
    # and synchronous; spending is opt-in, and the staircase showing two
    # different heights is the honest picture rather than a curated one.
    assert result.enriched == 37
    assert result.questions_passed == len(seed_mod.QUESTIONS)
    assert result.private_item_hidden
    # One record names the case; the rest join it on the deal identifier. If
    # this collapses to all-asserted or all-inferred, correlation has stopped
    # being demonstrable even though nothing errored.
    assert result.case_members == {"asserted": 1, "inferred": 39}


async def test_seeding_twice_is_refused(app_client):
    """A seeder that can run twice is a seeder that can overwrite a tenant."""
    app, client, drain = app_client
    await seed_demo(app.state.pool, client, drain=drain)

    with pytest.raises(SeedError) as exc:
        await seed_demo(app.state.pool, client, drain=drain)
    assert "--reset" in str(exc.value)


async def test_reset_purges_through_the_ordinary_cascade(app_client):
    """If resetting the demo needs bespoke cleanup, the purge cascade is
    incomplete — and the demo has found the bug before a customer did."""
    app, client, drain = app_client
    first = await seed_demo(app.state.pool, client, drain=drain)

    removed = await reset_demo(app.state.pool, client, drain=drain)
    assert removed["org_id"] == first.org_id
    assert removed["purged"] == 40
    # Users do not cascade from an org and must be removed by name, or the next
    # seed dies on the unique constraint over email.
    assert removed["users_removed"] == 2
    assert await seed_mod.already_seeded(app.state.pool) is None

    # And the ground is clean enough to seed onto again.
    second = await seed_demo(app.state.pool, client, drain=drain)
    assert second.org_id != first.org_id
    assert second.questions_passed == len(seed_mod.QUESTIONS)


async def test_reset_recovers_a_half_built_demo(app_client):
    """An interrupted seed leaves users behind with no org to hold them. Reset
    has to be able to finish a job it started, or the only way out is a
    hand-written DELETE against a live database."""
    app, client, drain = app_client
    await seed_demo(app.state.pool, client, drain=drain)
    # Exactly the debris a bootstrap that died after `create_user` leaves.
    await app.state.pool.execute(
        "DELETE FROM organizations WHERE name = $1", seed_mod.DEMO_ORG_NAME
    )

    removed = await reset_demo(app.state.pool, client, drain=drain)
    assert removed["org_id"] is None
    assert removed["users_removed"] == 2

    reseeded = await seed_demo(app.state.pool, client, drain=drain)
    assert reseeded.written == 40


async def test_every_record_is_visibly_synthetic(app_client):
    """The failure guarded against is a screenshot: a demo record that looks
    real may be treated as real by someone who was not there when it was
    seeded."""
    app, client, drain = app_client
    await seed_demo(app.state.pool, client, drain=drain)

    unmarked = await app.state.pool.fetchval(
        """
        SELECT count(*) FROM data_items d
        JOIN projects p ON p.project_id = d.project_id
        JOIN organizations o ON o.org_id = p.org_id
        WHERE o.name = $1 AND d.content_text NOT LIKE $2
        """,
        seed_mod.DEMO_ORG_NAME, f"%{seed_mod.MARKER}%",
    )
    assert unmarked == 0


async def test_the_corpus_uses_reserved_names_and_fake_identifiers():
    """A demo company that is a real company is a problem someone else did not
    agree to, and a realistic-format identifier can collide with a real one."""
    corpus = seed_mod._corpus()
    assert len(corpus) == 40

    for item in corpus:
        for identifier in item.payload()["identifiers"]:
            assert "DEMO" in identifier, f"{item.external_id} carries {identifier!r}"

    # Every question names a record that is actually in the corpus, so a
    # question cannot outlive the record that answers it.
    external_ids = {item.external_id for item in corpus}
    for question in seed_mod.QUESTIONS:
        assert question.must_find in external_ids
