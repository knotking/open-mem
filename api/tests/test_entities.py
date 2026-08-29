"""Entity resolution.

The hard part of a graph is not edges, it is deciding that "Priya", "Priya
Raman" and priya@example.com are one node while the other Priya in a different
thread is not.

The two errors are not symmetric, and every test here is shaped by that.
Under-merging leaves two nodes a person can join later. Over-merging silently
fuses two people's records, and once their mentions are interleaved nobody can
tell which fact belonged to whom. The resolver is tuned for the recoverable
error.
"""

from __future__ import annotations

import pytest

from memdog import entities
from memdog.entities import EntityError, get_entity, list_entities, merge, normalize, unmerge
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio


async def _item(pool, tenant, external_id="e-1", access="private"):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', $7, 'some text', now())
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, access,
    )
    return data_id


async def _resolve(pool, tenant, data_id, candidates):
    async with pool.acquire() as conn, conn.transaction():
        return await entities.resolve_mentions(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, candidates=candidates,
        )


# ------------------------------------------------------------- normalization

async def test_normalization_joins_real_variation():
    assert normalize("Dr. Priya Raman") == normalize("priya raman")
    assert normalize("Priya  Raman") == normalize("Priya Raman")
    assert normalize("José García") == normalize("Jose Garcia")
    assert normalize("Acme, Inc.") == normalize("Acme Inc")


async def test_normalization_does_not_join_different_names():
    """Where it would start guessing, it stops. Stripping initials or
    reordering words is where "J. Smith" begins matching "Smith, Jane" and two
    people become one permanently."""
    assert normalize("J. Smith") != normalize("Jane Smith")
    assert normalize("Priya Raman") != normalize("Priya Ramanathan")
    assert normalize("Smith, Jane") != normalize("Jane Smith")


# ---------------------------------------------------------------- resolution

async def test_the_same_name_in_two_records_is_one_entity(pool, tenant):
    a = await _item(pool, tenant, "a")
    b = await _item(pool, tenant, "b")
    first = await _resolve(pool, tenant, a, [{"name": "Priya Raman", "type": "person"}])
    second = await _resolve(pool, tenant, b, [{"name": "priya raman", "type": "person"}])

    assert first[0]["entity_id"] == second[0]["entity_id"]
    assert second[0]["resolved_by"] == "exact_name"


async def test_a_shared_identifier_joins_two_different_names(pool, tenant):
    """The only evidence strong enough to merge across names. A nickname and a
    full name share an email; nothing else could safely connect them."""
    a = await _item(pool, tenant, "a")
    b = await _item(pool, tenant, "b")
    first = await _resolve(pool, tenant, a, [
        {"name": "Priya Raman", "type": "person", "identifier": "priya@example.com"}])
    second = await _resolve(pool, tenant, b, [
        {"name": "P. Raman", "type": "person", "identifier": "PRIYA@example.com"}])

    assert first[0]["entity_id"] == second[0]["entity_id"]
    assert second[0]["resolved_by"] == "identifier"


async def test_two_people_sharing_a_first_name_stay_separate(pool, tenant):
    """The error that cannot be undone by looking at the data afterwards."""
    a = await _item(pool, tenant, "a")
    first = await _resolve(pool, tenant, a, [
        {"name": "Priya Raman", "type": "person"},
        {"name": "Priya Sharma", "type": "person"},
    ])
    assert len({r["entity_id"] for r in first}) == 2


async def test_a_person_and_an_organization_with_one_name_stay_separate(pool, tenant):
    a = await _item(pool, tenant, "a")
    resolved = await _resolve(pool, tenant, a, [
        {"name": "Morgan", "type": "person"},
        {"name": "Morgan", "type": "organization"},
    ])
    assert len({r["entity_id"] for r in resolved}) == 2


async def test_a_name_repeated_in_one_record_is_one_piece_of_evidence(pool, tenant):
    """Twenty mentions of a name in a document is one document mentioning it,
    and counting it twenty times would make document length look like
    importance."""
    a = await _item(pool, tenant, "a")
    await _resolve(pool, tenant, a, [{"name": "Acme", "type": "organization"}] * 5)
    count = await pool.fetchval(
        "SELECT count(*) FROM entity_mentions WHERE data_id = $1", a
    )
    assert count == 1


async def test_the_fullest_surface_form_becomes_the_label(pool, tenant):
    """Which form arrives first is an accident of ingestion order, and the
    label should not be."""
    a = await _item(pool, tenant, "a")
    b = await _item(pool, tenant, "b")
    await _resolve(pool, tenant, a, [
        {"name": "Acme", "type": "organization", "identifier": "acme.com"}])
    await _resolve(pool, tenant, b, [
        {"name": "Acme Corporation", "type": "organization", "identifier": "acme.com"}])

    name = await pool.fetchval(
        "SELECT display_name FROM entities WHERE $1 = ANY(identifiers)", "acme.com"
    )
    assert name == "Acme Corporation"


async def test_junk_candidates_are_discarded(pool, tenant):
    a = await _item(pool, tenant, "a")
    resolved = await _resolve(pool, tenant, a, [
        {"name": "", "type": "person"},
        {"name": "   ", "type": "person"},
        {"name": "Valid", "type": "not_a_type"},
        {"type": "person"},
    ])
    assert resolved == []


# ------------------------------------------------------------------- reading

async def test_an_entity_is_invisible_without_a_visible_mention(
    pool, tenant, other_tenant, principal_for
):
    """An entity is only visible through its mentions, and a mention inherits
    the ACL of its record. Listing an entity whose every mention is hidden
    would disclose that the record exists -- the exact thing its ACL forbids."""
    owner = await principal_for(tenant.api_key)
    a = await _item(pool, tenant, "secret", access="private")
    await _resolve(pool, tenant, a, [{"name": "Northwind", "type": "organization"}])

    assert any(e["display_name"] == "Northwind"
               for e in await list_entities(pool, owner, tenant.project_id))

    intruder = await principal_for(other_tenant.api_key)
    assert await list_entities(pool, intruder, other_tenant.project_id) == []


async def test_the_mention_count_reflects_what_the_caller_can_see(
    pool, tenant, principal_for
):
    """"42 mentions" shown against a list of three is itself a disclosure."""
    owner = await principal_for(tenant.api_key)
    a = await _item(pool, tenant, "a")
    resolved = await _resolve(pool, tenant, a, [{"name": "Acme", "type": "organization"}])
    entity = await get_entity(pool, owner, resolved[0]["entity_id"])
    assert entity["visible_mention_count"] == len(entity["mentions"]) == 1


async def test_every_mention_keeps_its_evidence(pool, tenant, principal_for):
    """A resolution nobody can inspect is one nobody can correct."""
    owner = await principal_for(tenant.api_key)
    a = await _item(pool, tenant, "a")
    resolved = await _resolve(pool, tenant, a, [
        {"name": "Dr. Priya Raman", "type": "person"}])
    entity = await get_entity(pool, owner, resolved[0]["entity_id"])
    mention = entity["mentions"][0]
    assert mention["surface"] == "Dr. Priya Raman", "the form as written survives"
    assert mention["resolved_by"] == "new"
    assert mention["data_id"] == a


# ------------------------------------------------------------------ merging

async def test_a_merge_moves_the_mentions_and_can_be_undone(
    pool, tenant, principal_for
):
    """A destructive merge would make "these are the same person" an
    irreversible claim, and people do not make irreversible claims -- so they
    would not correct the graph at all."""
    owner = await principal_for(tenant.api_key)
    a, b = await _item(pool, tenant, "a"), await _item(pool, tenant, "b")
    one = (await _resolve(pool, tenant, a, [{"name": "Bob Smith", "type": "person"}]))[0]
    two = (await _resolve(pool, tenant, b, [{"name": "Robert Smith", "type": "person"}]))[0]
    assert one["entity_id"] != two["entity_id"]

    result = await merge(pool, owner, source_id=one["entity_id"],
                         target_id=two["entity_id"], reason="same person")
    assert result["reversible"] is True

    target = await get_entity(pool, owner, two["entity_id"])
    assert {m["data_id"] for m in target["mentions"]} == {a, b}

    undone = await unmerge(pool, owner, result["merge_id"])
    assert undone["mentions_returned"] == 1
    restored = await get_entity(pool, owner, one["entity_id"])
    assert {m["data_id"] for m in restored["mentions"]} == {a}


async def test_a_merged_entity_stops_appearing_in_listings(pool, tenant, principal_for):
    owner = await principal_for(tenant.api_key)
    a, b = await _item(pool, tenant, "a"), await _item(pool, tenant, "b")
    one = (await _resolve(pool, tenant, a, [{"name": "Bob Smith", "type": "person"}]))[0]
    two = (await _resolve(pool, tenant, b, [{"name": "Robert Smith", "type": "person"}]))[0]
    await merge(pool, owner, source_id=one["entity_id"], target_id=two["entity_id"])

    listed = {e["entity_id"] for e in await list_entities(pool, owner, tenant.project_id)}
    assert one["entity_id"] not in listed
    assert two["entity_id"] in listed


async def test_new_mentions_of_a_merged_name_land_on_the_survivor(pool, tenant, principal_for):
    """Otherwise mentions accumulate on an entity nobody looks at, and the
    merge silently stops holding."""
    owner = await principal_for(tenant.api_key)
    a, b, c = [await _item(pool, tenant, x) for x in ("a", "b", "c")]
    one = (await _resolve(pool, tenant, a, [{"name": "Bob Smith", "type": "person"}]))[0]
    two = (await _resolve(pool, tenant, b, [{"name": "Robert Smith", "type": "person"}]))[0]
    await merge(pool, owner, source_id=one["entity_id"], target_id=two["entity_id"])

    later = await _resolve(pool, tenant, c, [{"name": "Bob Smith", "type": "person"}])
    assert later[0]["entity_id"] == two["entity_id"]


async def test_an_entity_cannot_be_merged_into_itself(pool, tenant, principal_for):
    owner = await principal_for(tenant.api_key)
    a = await _item(pool, tenant, "a")
    one = (await _resolve(pool, tenant, a, [{"name": "Acme", "type": "organization"}]))[0]
    with pytest.raises(EntityError):
        await merge(pool, owner, source_id=one["entity_id"], target_id=one["entity_id"])


async def test_another_org_cannot_merge_your_entities(
    pool, tenant, other_tenant, principal_for
):
    owner = await principal_for(tenant.api_key)
    a, b = await _item(pool, tenant, "a"), await _item(pool, tenant, "b")
    one = (await _resolve(pool, tenant, a, [{"name": "Bob Smith", "type": "person"}]))[0]
    two = (await _resolve(pool, tenant, b, [{"name": "Robert Smith", "type": "person"}]))[0]

    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(EntityError) as exc:
        await merge(pool, intruder, source_id=one["entity_id"], target_id=two["entity_id"])
    assert exc.value.status == 404


async def test_deleting_a_record_removes_its_mentions(pool, tenant, principal_for):
    """Mentions are personal data derived from a record. A cascade that left
    them behind is the compliance hole this schema exists to avoid."""
    owner = await principal_for(tenant.api_key)
    a = await _item(pool, tenant, "a")
    resolved = await _resolve(pool, tenant, a, [{"name": "Priya Raman", "type": "person"}])
    await pool.execute("DELETE FROM data_items WHERE data_id = $1", a)
    assert await pool.fetchval(
        "SELECT count(*) FROM entity_mentions WHERE entity_id = $1",
        resolved[0]["entity_id"],
    ) == 0
