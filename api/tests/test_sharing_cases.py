"""Sharing, cases and agent configuration.

Public sharing is the feature most likely to cause an accidental disclosure, so
most of these test a control rather than a capability.
"""

from __future__ import annotations

import pytest

from memdog import agents, cases, normalize, sharing
from memdog.contracts import CaseRef, Inline, WriteItem, WriteRequest
from memdog.settings_store import put
from memdog.sharing import ShareError
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, external_id, text="Body.", **kw):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=external_id, content=Inline(text=text), **kw),
        ]),
    )


async def _enable_sharing(pool, tenant):
    await put(pool, "public_sharing", True, scope="org", scope_id=tenant.org_id,
              set_by=tenant.user_id, org_id=tenant.org_id)


# ---------------------------------------------------------------- sharing


async def test_sharing_is_off_until_an_org_turns_it_on(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Some organisations never enable it, so the default cannot be 'allowed'."""
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "s-1")
    with pytest.raises(ShareError) as exc:
        await sharing.create_share(pool, actor, data_id=written.results[0].data_id)
    assert exc.value.status == 403


async def test_a_share_requires_an_expiry_within_bounds(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A link with no expiry is a permanent disclosure nobody revisits."""
    actor = await principal_for(tenant.api_key)
    await _enable_sharing(pool, tenant)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "s-2")
    for days in (0, 400):
        with pytest.raises(ShareError):
            await sharing.create_share(
                pool, actor, data_id=written.results[0].data_id, expires_in_days=days
            )


async def test_a_share_link_reads_the_item_and_nothing_derived(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Sharing a document must not publish its summary -- a summary spanning a
    public document and two private ones would leak two to share one."""
    actor = await principal_for(tenant.api_key)
    await _enable_sharing(pool, tenant)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                            "s-3", "The rollback completed at 14:02.")
    await queue.drain()
    assert await pool.fetchval("SELECT count(*) FROM artifacts") == 1

    share = await sharing.create_share(pool, actor, data_id=written.results[0].data_id)
    got = await sharing.resolve_share(pool, share["token"])
    assert got["kind"] == "data"
    assert "rollback completed" in got["item"]["text"]
    # No artifact, no summary, no keywords: the internal derived layer is not
    # part of what was shared.
    assert "summary" not in got["item"] and "keywords" not in got["item"]


async def test_every_share_access_is_logged_not_only_creation(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await _enable_sharing(pool, tenant)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "s-4")
    share = await sharing.create_share(pool, actor, data_id=written.results[0].data_id)
    await sharing.resolve_share(pool, share["token"])
    await sharing.resolve_share(pool, share["token"])

    row = await pool.fetchrow(
        "SELECT access_count FROM share_links WHERE share_id = $1", share["share_id"]
    )
    assert row["access_count"] == 2
    reads = await pool.fetch(
        "SELECT principal FROM access_log WHERE action = 'share.read' AND data_id = $1",
        written.results[0].data_id,
    )
    assert len(reads) == 2 and reads[0]["principal"] == "public"


async def test_revocation_takes_effect_immediately(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await _enable_sharing(pool, tenant)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "s-5")
    share = await sharing.create_share(pool, actor, data_id=written.results[0].data_id)
    await sharing.revoke_share(pool, actor, share["share_id"])

    with pytest.raises(ShareError) as exc:
        await sharing.resolve_share(pool, share["token"])
    assert exc.value.status == 410


async def test_you_cannot_share_what_you_cannot_see(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for
):
    """Sharing is not a way to widen your own access."""
    owner = await principal_for(tenant.api_key)
    intruder = await principal_for(other_tenant.api_key)
    await _enable_sharing(pool, tenant)
    await _enable_sharing(pool, other_tenant)
    written = await _write(pool, queue, blobs, settings, owner, tenant.producer_id, "s-6")

    with pytest.raises(ShareError) as exc:
        await sharing.create_share(pool, intruder, data_id=written.results[0].data_id)
    assert exc.value.status == 404


async def test_the_inventory_lists_what_is_live(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await _enable_sharing(pool, tenant)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "s-7")
    share = await sharing.create_share(pool, actor, data_id=written.results[0].data_id)

    rows = {r["share_id"]: r for r in await sharing.inventory(pool, actor)}
    assert rows[share["share_id"]]["live"] is True
    await sharing.revoke_share(pool, actor, share["share_id"])
    rows = {r["share_id"]: r for r in await sharing.inventory(pool, actor)}
    assert rows[share["share_id"]]["live"] is False


# ------------------------------------------------------------------ cases


async def test_a_timeline_orders_by_event_time_not_ingestion(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A timeline built on when we learned about something renders perfectly
    while being wrong."""
    from datetime import datetime, timezone

    actor = await principal_for(tenant.api_key)
    case = await cases.create_case(
        pool, actor, project_id=tenant.project_id, case_type="patient",
        external_id="MRN-A12345", title="Patient A",
    )
    for external_id, year in (("recent", 2026), ("old", 2019)):
        await write_items(
            pool, queue, blobs, settings, actor,
            WriteRequest(producer_id=tenant.producer_id, items=[
                WriteItem(external_id=external_id, content=Inline(text=f"Note from {year}"),
                          event_time=datetime(year, 3, 14, tzinfo=timezone.utc),
                          case=CaseRef(external_id="MRN-A12345", case_type="patient")),
            ]),
        )
    timeline = await cases.timeline(pool, actor, case["case_id"])
    years = [e["event_time"].year for e in timeline["entries"]]
    assert years == [2019, 2026]      # backfilled record does not appear as today
    assert timeline["asserted"] == 2 and timeline["inferred"] == 0


async def test_membership_records_asserted_versus_inferred(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A timeline that cannot tell them apart silently includes someone else's
    records."""
    actor = await principal_for(tenant.api_key)
    case = await cases.create_case(
        pool, actor, project_id=tenant.project_id, case_type="patient",
        external_id="MRN-B999", identifiers=["MRN-B999"],
    )
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="lab-1", content=Inline(text="Lab result."),
                      identifiers=["MRN-B999"]),
        ]),
    )
    timeline = await cases.timeline(pool, actor, case["case_id"])
    assert timeline["inferred"] == 1
    assert timeline["entries"][0]["matched_on"] == "MRN-B999"


# ---------------------------------------------------------- agent configs


async def test_an_override_makes_old_artifacts_stale(pool, tenant, principal_for):
    """Changing a prompt is a versioning event, and the consequence is stated
    rather than discovered."""
    actor = await principal_for(tenant.api_key)
    before = await agents.effective_config(
        pool, data_type="message_email", org_id=tenant.org_id, project_id=tenant.project_id
    )
    assert before["source"] == "shipped" and not before["overridden"]

    after = await agents.set_config(
        pool, actor, data_type="message_email",
        prompt="Extract only the sender and the deadline.",
        scope="project", project_id=tenant.project_id,
    )
    assert after["overridden"] and after["source"] == "project"
    assert after["artifacts_now_stale"] is True
    assert after["generator_version"] != before["generator_version"]


async def test_an_org_lock_stops_a_project_replacing_an_approved_prompt(
    pool, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await agents.set_config(
        pool, actor, data_type="document", prompt="Approved wording.",
        scope="org", lock=True,
    )
    with pytest.raises(agents.AgentConfigError) as exc:
        await agents.set_config(
            pool, actor, data_type="document", prompt="Something else.",
            scope="project", project_id=tenant.project_id,
        )
    assert exc.value.status == 409

    effective = await agents.effective_config(
        pool, data_type="document", org_id=tenant.org_id, project_id=tenant.project_id
    )
    assert effective["prompt"] == "Approved wording."
    assert effective["source"] == "org (locked)"


# ----------------------------------------------------------- normalization


async def test_a_projection_is_stored_separately_and_mirrors_identifiers(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await normalize.create_schema(
        pool, actor, project_id=tenant.project_id, target_type="Invoice",
        fields={"number": {"required": True}, "customer": {}, "amount": {}},
        mapping={"number": "id", "customer": "account.name", "amount": "lines[0].amount",
                 "identifier_fields": ["number"]},
    )
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id, "inv-1",
        '{"id": "INV-2291", "account": {"name": "Acme"}, "lines": [{"amount": 12400}]}',
    )
    data_id = written.results[0].data_id
    result = await normalize.project(
        pool, data_id=data_id, project_id=tenant.project_id,
        text='{"id": "INV-2291", "account": {"name": "Acme"}, "lines": [{"amount": 12400}]}',
        data_type="structured_json",
    )
    assert result["payload"] == {"number": "INV-2291", "customer": "Acme", "amount": 12400}

    # The original is untouched: a projection is a derived view, not a
    # replacement.
    original = await pool.fetchval("SELECT content_text FROM data_items WHERE data_id = $1", data_id)
    assert '"id": "INV-2291"' in original
    # And identifiers are mirrored because correlation joins on them.
    assert await pool.fetchval(
        "SELECT identifiers FROM data_items WHERE data_id = $1", data_id
    ) == ["INV-2291"]


async def test_a_normalization_failure_keeps_the_record_and_stays_retryable(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A schema that cannot parse one record is not grounds for losing it."""
    actor = await principal_for(tenant.api_key)
    await normalize.create_schema(
        pool, actor, project_id=tenant.project_id, target_type="Invoice",
        fields={"number": {"required": True}}, mapping={"number": "id"},
    )
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                            "inv-bad", '{"no_id": true}')
    data_id = written.results[0].data_id
    result = await normalize.project(
        pool, data_id=data_id, project_id=tenant.project_id,
        text='{"no_id": true}', data_type="structured_json",
    )
    assert result is None
    row = await pool.fetchrow(
        "SELECT status, failure_reason FROM normalized_records WHERE data_id = $1", data_id
    )
    assert row["status"] == "failed" and "missing required" in row["failure_reason"]
    # The item itself is intact and still searchable.
    assert await pool.fetchval("SELECT content_text FROM data_items WHERE data_id = $1", data_id)
