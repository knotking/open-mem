"""Everything known about one memory, in one request.

A memory is the container people actually think in -- "the Acme thread", "the
Gita" -- and the console could report how many records were in one and nothing
else. To learn what it was *about* you opened Data, filtered, opened a record,
read its keywords, then opened Entities and guessed which of them came from
here. The information existed in four places and belonged in one.

The part worth testing hardest is the ACL. A memory you can see may hold records
you cannot, and a summary that counted them would report a corpus you are not
allowed to read -- which is the same structural leak the graph traversal is
careful about, in a place nobody would think to look for it.
"""

from __future__ import annotations

import pytest

from memdog.auth import ApiKeyVerifier
from memdog.entities import resolve_mentions
from memdog.graph import record_edges
from memdog.ids import new_id
from memdog.memories import context

pytestmark = pytest.mark.asyncio


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _memory(pool, tenant, key="ctx"):
    memory_id = new_id("mem")
    await pool.execute(
        """
        INSERT INTO memories (memory_id, org_id, project_id, owner_id, type,
                              memory_key, title)
        VALUES ($1, $2, $3, $4, 'default', $5, 'Context test')
        """,
        memory_id, tenant.org_id, tenant.project_id, tenant.user_id,
        f"{key}-{new_id('k')}",
    )
    return memory_id


async def _member(pool, tenant, memory_id, *, state="enriched", access="org",
                  owner=None, template=None, keywords=(), entities=(), relations=()):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time, template)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'text', now(), $9)
        """,
        data_id, tenant.org_id, tenant.project_id, owner or tenant.user_id,
        tenant.producer_id, new_id("ext"), state, access, template,
    )
    await pool.execute(
        "INSERT INTO memory_members (memory_id, data_id, added_by) VALUES ($1,$2,'explicit')",
        memory_id, data_id,
    )
    if keywords:
        artifact_id = new_id("art")
        # `generator_version` is a foreign key, so the generator has to exist
        # before the artifact that names it -- the same rule the worker follows.
        generator = f"gen_test_{new_id('g')}"
        await pool.execute(
            "INSERT INTO generators (generator_version, purpose, model_id, spec)"
            " VALUES ($1,'extract','test-model','{}'::jsonb)"
            " ON CONFLICT (generator_version) DO NOTHING", generator,
        )
        await pool.execute(
            """
            INSERT INTO artifacts (artifact_id, org_id, project_id, owner_id, kind,
                                   title, keywords, access_level, model_id,
                                   generator_version, served_by_model)
            VALUES ($1,$2,$3,$4,'envelope','t',$5,'org','test-model',$6,'test-model')
            """,
            artifact_id, tenant.org_id, tenant.project_id, tenant.user_id,
            list(keywords), generator,
        )
        await pool.execute(
            "INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end)"
            " VALUES ($1,$2,0,4)", artifact_id, data_id,
        )
    if entities:
        async with pool.acquire() as conn, conn.transaction():
            resolved = await resolve_mentions(
                conn, data_id=data_id, org_id=tenant.org_id,
                project_id=tenant.project_id, candidates=list(entities),
            )
            if relations:
                await record_edges(
                    conn, data_id=data_id, org_id=tenant.org_id,
                    project_id=tenant.project_id, resolved=resolved,
                    relations=list(relations), template=template,
                )
    return data_id


FIGURES = [{"name": "Krishna", "type": "person"},
           {"name": "karma yoga", "type": "topic"}]


async def test_it_reports_records_keywords_entities_and_edges_together(pool, tenant):
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, template="scripture",
                  keywords=["dharma", "karma yoga"], entities=FIGURES,
                  relations=[{"subject": "Krishna", "predicate": "teaches",
                              "object": "karma yoga"}])
    await _member(pool, tenant, memory_id, state="stored")

    held = await context(pool, await _principal(pool, tenant), memory_id)

    assert held["records"] == {"total": 2, "stored": 1, "searchable": 0, "enriched": 1}
    assert held["templates"] == [{"template": "scripture", "records": 1}]
    assert {k["keyword"] for k in held["keywords"]} == {"dharma", "karma yoga"}
    assert {e["display_name"] for e in held["entities"]} == {"Krishna", "karma yoga"}
    assert len(held["edges"]) == 1
    edge = held["edges"][0]
    assert (edge["subject"], edge["predicate"], edge["object"]) == (
        "Krishna", "teaches", "karma yoga")
    assert edge["template"] == "scripture"
    # `teaches` is a reading of what the text argues, not a sentence it stated.
    assert edge["confidence_class"] == "interpretive"


async def test_records_the_caller_cannot_read_are_not_counted(pool, tenant):
    """A memory you can see may hold records you cannot, and summarising those
    reports a corpus you are not allowed to read."""
    from memdog.bootstrap import create_user

    other = await create_user(pool, f"outsider-{new_id('x')}@example.com")
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, keywords=["visible"], entities=FIGURES)
    await _member(pool, tenant, memory_id, access="private", owner=other,
                  keywords=["secret"],
                  entities=[{"name": "Hidden Figure", "type": "person"}])

    held = await context(pool, await _principal(pool, tenant), memory_id)

    assert held["records"]["total"] == 1
    assert [k["keyword"] for k in held["keywords"]] == ["visible"]
    assert "Hidden Figure" not in {e["display_name"] for e in held["entities"]}


async def test_a_memory_nobody_can_see_is_not_found_rather_than_forbidden(pool, tenant):
    from memdog.retrieval import NotFound

    with pytest.raises(NotFound):
        await context(pool, await _principal(pool, tenant), "mem_does_not_exist")


async def test_an_empty_memory_reports_zeros_rather_than_failing(pool, tenant):
    """"Nothing in it" and "it does not exist" are different facts, and the
    screen renders them differently."""
    memory_id = await _memory(pool, tenant)
    held = await context(pool, await _principal(pool, tenant), memory_id)
    assert held["records"]["total"] == 0
    assert held["keywords"] == [] and held["entities"] == [] and held["edges"] == []
