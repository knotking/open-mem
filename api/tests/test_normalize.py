"""Normalization, now that it runs.

`project()` existed, was correct, and was called by nothing: a schema could be
registered through the API and would never be applied, so `identifiers` was
whatever the writer restated and the projection table stayed empty. These tests
are about the wiring as much as the projection — most of them would have passed
against the dead version, which is exactly why the ones that would not are here.
"""

from __future__ import annotations

import json

import pytest

from open_mem import normalize
from open_mem.auth import ApiKeyVerifier
from open_mem.contracts import Inline, WriteItem, WriteOptions, WriteRequest
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio

# A structured record shaped like something a CRM would send. `deal_id` is the
# field correlation actually cares about; it is nowhere in the write request.
INVOICE_FIELDS = {
    "deal_id": {"required": True},
    "amount": {"required": True},
    "customer": {"required": False},
}
INVOICE_MAPPING = {
    "data_type": "invoice",
    "deal_id": "meta.deal",
    "amount": "totals.gross",
    "customer": "party.name",
    "identifier_fields": ["deal_id"],
}


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _schema(pool, tenant, *, fields=None, mapping=None):
    return await normalize.create_schema(
        pool, await _principal(pool, tenant),
        project_id=tenant.project_id,
        target_type="invoice",
        fields=fields or INVOICE_FIELDS,
        mapping=mapping or INVOICE_MAPPING,
    )


async def _write(pool, queue, blobs, settings, tenant, *, text, data_type="invoice",
                 external_id="rec-1", identifiers=None, case=None):
    return await write_items(
        pool, queue, blobs, settings,
        await _principal(pool, tenant),
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(
                external_id=external_id,
                content=Inline(text=text),
                data_type=data_type,
                identifiers=identifiers or [],
                case=case,
            )],
            options=WriteOptions(enrich=False),
        ),
        None,
    )


async def _record(pool, data_id):
    return await pool.fetchrow(
        "SELECT * FROM normalized_records WHERE data_id = $1", data_id
    )


async def test_a_registered_schema_is_actually_applied(pool, queue, blobs, settings, tenant):
    """The defect this closes: a schema could be registered and never run."""
    await _schema(pool, tenant)
    payload = json.dumps({
        "meta": {"deal": "DEAL-77"},
        "totals": {"gross": 1450},
        "party": {"name": "Acme"},
    })

    result = await _write(pool, queue, blobs, settings, tenant, text=payload)
    data_id = result.results[0].data_id

    row = await _record(pool, data_id)
    assert row is not None, "no projection was written"
    assert row["status"] == "ok"
    assert row["target_type"] == "invoice"
    assert row["payload"] == {"deal_id": "DEAL-77", "amount": 1450, "customer": "Acme"}
    assert list(row["identifiers"]) == ["DEAL-77"]


async def test_the_projected_identifier_reaches_the_item(pool, queue, blobs, settings, tenant):
    """Mirrored onto `data_items` because correlation joins on it, and the join
    must not require a second table."""
    await _schema(pool, tenant)
    result = await _write(
        pool, queue, blobs, settings, tenant,
        text=json.dumps({"meta": {"deal": "DEAL-88"}, "totals": {"gross": 10}}),
    )
    identifiers = await pool.fetchval(
        "SELECT identifiers FROM data_items WHERE data_id = $1",
        result.results[0].data_id,
    )
    assert "DEAL-88" in identifiers


async def test_a_caller_supplied_identifier_survives_the_projection(
    pool, queue, blobs, settings, tenant
):
    """An identifier the writer sent is a fact they know and the schema does
    not. Assigning rather than merging would silently drop it."""
    await _schema(pool, tenant)
    result = await _write(
        pool, queue, blobs, settings, tenant,
        text=json.dumps({"meta": {"deal": "DEAL-99"}, "totals": {"gross": 5}}),
        identifiers=["MRN-DEMO-1"],
    )
    identifiers = set(await pool.fetchval(
        "SELECT identifiers FROM data_items WHERE data_id = $1",
        result.results[0].data_id,
    ))
    assert identifiers == {"DEAL-99", "MRN-DEMO-1"}


async def test_a_projected_identifier_correlates_into_a_case(
    pool, queue, blobs, settings, tenant
):
    """The reason normalization runs *before* correlation rather than after.

    The second record never mentions the case or the deal id in its write
    request — both come out of the payload — so a projection that ran after
    `route_case` would correlate on nothing.
    """
    await _schema(pool, tenant)
    from open_mem.contracts import CaseRef

    first = await _write(
        pool, queue, blobs, settings, tenant, external_id="anchor",
        text=json.dumps({"meta": {"deal": "DEAL-CORR"}, "totals": {"gross": 1}}),
        case=CaseRef(external_id="DEAL-CORR", case_type="opportunity"),
    )
    second = await _write(
        pool, queue, blobs, settings, tenant, external_id="follows",
        text=json.dumps({"meta": {"deal": "DEAL-CORR"}, "totals": {"gross": 2}}),
    )

    members = await pool.fetch(
        """
        SELECT cm.data_id, cm.basis, cm.matched_on
        FROM case_members cm JOIN cases c ON c.case_id = cm.case_id
        WHERE c.external_id = 'DEAL-CORR'
        """
    )
    by_id = {m["data_id"]: m for m in members}
    assert by_id[first.results[0].data_id]["basis"] == "asserted"
    assert by_id[second.results[0].data_id]["basis"] == "inferred"
    # Recorded so a wrong correlation can be traced to its cause.
    assert by_id[second.results[0].data_id]["matched_on"] == "DEAL-CORR"


async def test_a_missing_required_field_lands_the_record_raw_with_a_reason(
    pool, queue, blobs, settings, tenant
):
    """A schema that cannot read one record is not grounds for losing it."""
    await _schema(pool, tenant)
    result = await _write(
        pool, queue, blobs, settings, tenant,
        text=json.dumps({"meta": {"deal": "DEAL-11"}}),   # no totals.gross
    )
    assert result.results[0].status == "created"          # the write is not rejected
    data_id = result.results[0].data_id

    row = await _record(pool, data_id)
    assert row["status"] == "failed"
    assert "amount" in row["failure_reason"]
    # And the original is intact, which is the whole point of a projection being
    # a derived view rather than a replacement.
    assert await pool.fetchval(
        "SELECT content_text IS NOT NULL FROM data_items WHERE data_id = $1", data_id
    )


async def test_content_that_is_not_json_fails_the_same_way(
    pool, queue, blobs, settings, tenant
):
    await _schema(pool, tenant)
    result = await _write(
        pool, queue, blobs, settings, tenant, text="this is prose, not a record"
    )
    row = await _record(pool, result.results[0].data_id)
    assert row["status"] == "failed"
    assert row["failure_reason"] == "content is not JSON"


async def test_a_project_with_no_schema_is_untouched(
    pool, queue, blobs, settings, tenant
):
    """Normalization is opt-in per project. With nothing registered, behaviour
    is exactly what it was before this was wired up."""
    result = await _write(
        pool, queue, blobs, settings, tenant,
        text=json.dumps({"meta": {"deal": "DEAL-NONE"}}),
    )
    data_id = result.results[0].data_id
    assert await _record(pool, data_id) is None
    assert list(await pool.fetchval(
        "SELECT identifiers FROM data_items WHERE data_id = $1", data_id
    )) == []


async def test_a_schema_scoped_to_a_data_type_ignores_everything_else(
    pool, queue, blobs, settings, tenant
):
    """`data_type` in the mapping is what keeps a schema from marking every
    prose record in the project a normalization failure."""
    await _schema(pool, tenant)
    result = await _write(
        pool, queue, blobs, settings, tenant,
        text="an ordinary email", data_type="email", external_id="an-email",
    )
    assert await _record(pool, result.results[0].data_id) is None


async def test_the_projection_is_replaced_when_the_record_is_rewritten(
    pool, queue, blobs, settings, tenant
):
    """Same natural key, new payload. A stale projection outliving its source is
    the same defect as a stale vector."""
    await _schema(pool, tenant)
    await _write(
        pool, queue, blobs, settings, tenant, external_id="same",
        text=json.dumps({"meta": {"deal": "OLD"}, "totals": {"gross": 1}}),
    )
    result = await _write(
        pool, queue, blobs, settings, tenant, external_id="same",
        text=json.dumps({"meta": {"deal": "NEW"}, "totals": {"gross": 2}}),
    )
    row = await _record(pool, result.results[0].data_id)
    assert row["payload"]["deal_id"] == "NEW"
    assert row["status"] == "ok"


async def test_a_failed_projection_is_repaired_by_a_later_good_write(
    pool, queue, blobs, settings, tenant
):
    """Failures stay retryable: the record is raw with a reason, not poisoned."""
    await _schema(pool, tenant)
    await _write(
        pool, queue, blobs, settings, tenant, external_id="repair",
        text=json.dumps({"meta": {"deal": "DEAL-R"}}),
    )
    result = await _write(
        pool, queue, blobs, settings, tenant, external_id="repair",
        text=json.dumps({"meta": {"deal": "DEAL-R"}, "totals": {"gross": 3}}),
    )
    row = await _record(pool, result.results[0].data_id)
    assert row["status"] == "ok"
    assert row["failure_reason"] is None


async def test_the_projection_shares_the_write_transaction(pool, tenant):
    """It takes a connection, not a pool.

    `normalized_records` references `data_items`, so a projection written on its
    own connection either races the insert it describes or survives a write that
    rolled back — and both leave a projection pointing at nothing.
    """
    import inspect

    assert "conn" in inspect.signature(normalize.project).parameters
    assert "pool" not in inspect.signature(normalize.project).parameters

    await _schema(pool, tenant)
    async with pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        await conn.execute(
            """
            INSERT INTO data_items (data_id, org_id, project_id, producer_id,
                                    owner_id, external_id, access_level, state,
                                    event_time, content_text)
            VALUES ('dat_rollback', $1, $2, $3, $4, 'rollback', 'private',
                    'stored', now(), '{}')
            """,
            tenant.org_id, tenant.project_id, tenant.producer_id, tenant.user_id,
        )
        await normalize.project(
            conn, data_id="dat_rollback", project_id=tenant.project_id,
            text=json.dumps({"meta": {"deal": "GONE"}, "totals": {"gross": 1}}),
            data_type="invoice",
        )
        await transaction.rollback()

    assert await _record(pool, "dat_rollback") is None
