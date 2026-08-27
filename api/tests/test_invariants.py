"""The invariant gate.

These are the exit criterion for the slice, not a nice-to-have alongside it.
Three properties, chosen because each one is silent when it breaks:

1. **Cross-tenant isolation on every retrieval path.** A leak that only exists in
   the lexical arm is still a leak.
2. **Durability after a 2xx.** If the process dies between the response and the
   enrichment, the item is still there.
3. **One vector space per index.** `distinct(model_id) == 1`, checked as a
   property rather than asserted in prose.
"""

from __future__ import annotations

import pytest

from memdog.auth import DATA_READ, DATA_WRITE, issue_key
from memdog.bootstrap import create_user
from memdog.contracts import (
    Inline,
    ItemAccess,
    RetrieveFilter,
    RetrieveRequest,
    WriteItem,
    WriteRequest,
)
from memdog.ids import new_id
from memdog.inference import LocalHashEmbedder
from memdog.queue import InProcessQueue
from memdog.retrieval import NotFound, get_item, retrieve
from memdog.workers import EmbedWorker
from memdog.write import EMBED_TOPIC, write_items

pytestmark = pytest.mark.asyncio

SECRET = "The acquisition price was eighty four million dollars."
ALL_MATCH_MODES = (["vector"], ["lexical"], ["vector", "lexical"])


async def _write(pool, queue, blobs, settings, actor, producer_id, items):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=items),
    )


async def _second_member(pool, tenant, *, capabilities=None):
    """Another human in the same org -- the case ACLs actually have to get right."""
    user_id = await create_user(pool, f"member-{new_id('x')}@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        user_id,
        tenant.org_id,
    )
    token = await issue_key(
        pool,
        user_id=user_id,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        capabilities=capabilities or [DATA_READ, DATA_WRITE],
    )
    return user_id, token


@pytest.mark.parametrize("match", ALL_MATCH_MODES)
async def test_no_cross_tenant_read_on_any_retrieval_path(
    pool, queue, blobs, settings, embedder, tenant, other_tenant, principal_for, match
):
    owner = await principal_for(tenant.api_key)
    intruder = await principal_for(other_tenant.api_key)

    written = await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="secret-1", content=Inline(text=SECRET),
                   access=ItemAccess(level="org"))],
    )
    await queue.drain()
    data_id = written.results[0].data_id

    # The owner finds it.
    mine = await retrieve(
        pool, embedder, owner,
        RetrieveRequest(query="acquisition price", match=match,
                        filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert [c.data_id for c in mine.results] == [data_id]

    # Pointing another org's credential at the project id finds nothing --
    # the predicate is inside the query, so there is no result to post-filter.
    theirs = await retrieve(
        pool, embedder, intruder,
        RetrieveRequest(query="acquisition price", match=match,
                        filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert theirs.results == []

    with pytest.raises(NotFound):
        await get_item(pool, intruder, data_id)


@pytest.mark.parametrize("match", ALL_MATCH_MODES)
async def test_private_items_are_invisible_to_other_members(
    pool, queue, blobs, settings, embedder, tenant, principal_for, match
):
    owner = await principal_for(tenant.api_key)
    _, colleague_key = await _second_member(pool, tenant)
    colleague = await principal_for(colleague_key)

    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="private-1", content=Inline(text=SECRET))],
    )
    await queue.drain()

    request = RetrieveRequest(query="acquisition price", match=match,
                              filter=RetrieveFilter(project_id=tenant.project_id))
    assert (await retrieve(pool, embedder, owner, request)).results
    assert (await retrieve(pool, embedder, colleague, request)).results == []


async def test_sharing_with_a_group_takes_effect_at_query_time(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """Resolution at query time is what makes revocation work -- the direction
    that matters. No shared row is rewritten when membership changes."""
    owner = await principal_for(tenant.api_key)
    colleague_id, colleague_key = await _second_member(pool, tenant)

    group_id = new_id("grp")
    await pool.execute(
        "INSERT INTO groups (group_id, org_id, name) VALUES ($1, $2, 'engineering')",
        group_id, tenant.org_id,
    )
    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="shared-1", content=Inline(text=SECRET),
                   access=ItemAccess(level="shared", principals=[f"group:{group_id}"]))],
    )
    await queue.drain()
    request = RetrieveRequest(query="acquisition price",
                              filter=RetrieveFilter(project_id=tenant.project_id))

    assert (await retrieve(pool, embedder, await principal_for(colleague_key), request)).results == []

    await pool.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES ($1, $2)", group_id, colleague_id
    )
    # Same key, re-resolved: the principal set is computed per request.
    assert (await retrieve(pool, embedder, await principal_for(colleague_key), request)).results

    await pool.execute("DELETE FROM group_members WHERE user_id = $1", colleague_id)
    assert (await retrieve(pool, embedder, await principal_for(colleague_key), request)).results == []


async def test_restricted_excludes_the_owner(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    owner = await principal_for(tenant.api_key)
    colleague_id, colleague_key = await _second_member(pool, tenant)
    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="restricted-1", content=Inline(text=SECRET),
                   access=ItemAccess(level="restricted", principals=[f"user:{colleague_id}"]))],
    )
    await queue.drain()
    request = RetrieveRequest(query="acquisition price",
                              filter=RetrieveFilter(project_id=tenant.project_id))
    assert (await retrieve(pool, embedder, owner, request)).results == []
    assert (await retrieve(pool, embedder, await principal_for(colleague_key), request)).results


async def test_a_shared_connection_writes_org_visible_items(
    pool, queue, blobs, settings, embedder, principal_for
):
    """The ACL follows the connection scope, and nothing the caller sends
    reaches that decision."""
    from memdog.bootstrap import bootstrap_tenant

    shared = await bootstrap_tenant(pool, org_name="shared-co", email="c@example.com",
                                    connection_scope="shared")
    owner = await principal_for(shared.api_key)
    _, colleague_key = await _second_member(pool, shared)

    await _write(
        pool, queue, blobs, settings, owner, shared.producer_id,
        [WriteItem(external_id="team-1", content=Inline(text=SECRET))],
    )
    await queue.drain()
    request = RetrieveRequest(query="acquisition price",
                              filter=RetrieveFilter(project_id=shared.project_id))
    assert (await retrieve(pool, embedder, await principal_for(colleague_key), request)).results


async def test_durable_after_2xx_even_if_enrichment_never_runs(
    pool, blobs, settings, embedder, tenant, principal_for
):
    """Kill the process after the response: the item survives, and the queue
    is what is behind -- not the data."""
    owner = await principal_for(tenant.api_key)
    orphan_queue = InProcessQueue()  # published to, never subscribed

    response = await write_items(
        pool, orphan_queue, blobs, settings, owner,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="durable-1", content=Inline(text=SECRET))]),
    )
    data_id = response.results[0].data_id
    assert await orphan_queue.depth() == 1

    item = await get_item(pool, owner, data_id)
    assert item["content_text"] == SECRET and item["state"] == "stored"
    assert await pool.fetchval("SELECT count(*) FROM chunks WHERE data_id = $1", data_id) == 0

    # A worker started later picks it up from the same durable row.
    worker = EmbedWorker(pool, embedder, settings)
    await worker.ensure_generator()
    worker.register(orphan_queue, EMBED_TOPIC)
    await orphan_queue.drain()
    await orphan_queue.close()
    assert (await get_item(pool, owner, data_id))["state"] == "searchable"


async def test_index_holds_exactly_one_vector_space(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """The property that a fallback chain silently violates.

    A second engine is used deliberately here: its rows must be identifiable,
    and retrieval must not consider them, because they are not comparable.
    """
    owner = await principal_for(tenant.api_key)
    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="v-1", content=Inline(text=SECRET))],
    )
    await queue.drain()

    other = LocalHashEmbedder(settings.embed_dim)
    other.model_id = "some-other-model-v2"
    other_worker = EmbedWorker(pool, other, settings)
    await other_worker.ensure_generator()
    await pool.execute("DELETE FROM chunks")  # simulate a rebuild under a second model
    from memdog.queue import Message

    await other_worker.handle(Message(EMBED_TOPIC, {"data_id": (
        await pool.fetchval("SELECT data_id FROM data_items")
    )}))

    models = {r["model_id"] for r in await pool.fetch("SELECT model_id FROM embeddings")}
    assert models == {"some-other-model-v2"}

    # Retrieval is pinned to the configured engine, so the foreign rows are
    # invisible rather than silently mixed into the ranking.
    found = await retrieve(
        pool, embedder, owner,
        RetrieveRequest(query="acquisition price", match=["vector"],
                        filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert found.results == []
    assert found.model_id == embedder.model_id


async def test_capabilities_are_enforced_separately_from_identity(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    from memdog.auth import AuthError

    _, read_only = await _second_member(pool, tenant, capabilities=[DATA_READ])
    reader = await principal_for(read_only)
    with pytest.raises(AuthError) as exc:
        await _write(
            pool, queue, blobs, settings, reader, tenant.producer_id,
            [WriteItem(external_id="nope", content=Inline(text="x"))],
        )
    assert exc.value.status == 403


async def test_leaving_the_org_revokes_the_key(pool, tenant, principal_for):
    """Membership-coupled, or offboarding leaks through a key nobody deleted."""
    from memdog.auth import AuthError

    await principal_for(tenant.api_key)  # works while a member
    await pool.execute("DELETE FROM memberships WHERE user_id = $1", tenant.user_id)
    with pytest.raises(AuthError):
        await principal_for(tenant.api_key)


async def test_startup_refuses_an_index_it_cannot_write_to(pool, settings, embedder):
    """The dimension is a property of the corpus, not of the process.

    This was a live bug before it was a test: a process configured for a
    different dimension wrote nothing, retried, and gave up -- and the symptom
    was items that stayed `stored` forever, which looks exactly like ordinary
    enrichment lag.
    """
    from memdog.workers import IndexDimensionMismatch, verify_index_dimension

    await verify_index_dimension(pool, embedder)  # matching: no complaint

    wrong = LocalHashEmbedder(settings.embed_dim + 8)
    with pytest.raises(IndexDimensionMismatch) as exc:
        await verify_index_dimension(pool, wrong)
    assert str(settings.embed_dim) in str(exc.value)


async def test_a_failing_handler_is_not_silent(pool, blobs, settings, tenant, principal_for):
    """A handler that fails every time must not be indistinguishable from an
    idle queue."""
    owner = await principal_for(tenant.api_key)
    queue = InProcessQueue(max_attempts=2, base_delay=0.001)

    async def always_fails(message):
        raise RuntimeError("engine is down")

    queue.subscribe(EMBED_TOPIC, always_fails)
    await write_items(
        pool, queue, blobs, settings, owner,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="dlq-1", content=Inline(text=SECRET))]),
    )
    await queue.drain()
    await queue.close()

    assert len(queue.dead_letters) == 1
    assert "engine is down" in queue.dead_letters[0][1]
    # The item is untouched and still retryable -- deferred, not lost.
    assert (await get_item(pool, owner, (await pool.fetchval(
        "SELECT data_id FROM data_items"))))["state"] == "stored"
