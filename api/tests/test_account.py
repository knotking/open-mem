"""Account deletion, and the line it must not cross.

"Delete my data" is two scopes wearing one name. Getting the boundary wrong
destroys a colleague's work as a side effect of an HR event.
"""

from __future__ import annotations

import pytest

from open_mem import account
from open_mem.account import AccountError
from open_mem.auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, issue_key
from open_mem.bootstrap import bootstrap_tenant, create_user
from open_mem.contracts import Inline, ItemAccess, WriteItem, WriteOptions, WriteRequest
from open_mem.retrieval import NotFound, get_item
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, external_id, **kw):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=external_id, content=Inline(text="Some content."), **kw),
        ], options=WriteOptions(enrich=False)),
    )


async def test_personal_data_goes_and_shared_data_stays(
    pool, queue, blobs, settings, principal_for
):
    """Data that arrived through a shared connection belongs to the
    organisation; the connection being one person's is an implementation
    detail of how it got here."""
    shared = await bootstrap_tenant(pool, org_name="leaver-co", email="leaver@example.com",
                                    connection_scope="shared")
    actor = await principal_for(shared.api_key)

    # Through the shared connection -- the team's.
    await _write(pool, queue, blobs, settings, actor, shared.producer_id, "team-1")

    # A personal producer for the same user.
    from open_mem.ids import new_id

    personal_conn, personal_prod = new_id("conn"), new_id("key")
    await pool.execute(
        """
        INSERT INTO connections (connection_id, org_id, project_id, user_id, provider, scope)
        VALUES ($1, $2, $3, $4, 'manual', 'personal')
        """,
        personal_conn, shared.org_id, shared.project_id, shared.user_id,
    )
    await pool.execute(
        """
        INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                               connection_id, status, inbound_auth)
        VALUES ($1, 'client', $2, $3, $4, $5, 'enabled', 'none')
        """,
        personal_prod, shared.user_id, shared.org_id, shared.project_id, personal_conn,
    )
    private = await _write(pool, queue, blobs, settings, actor, personal_prod, "mine-1")

    proposal = await account.plan(pool, shared.org_id, shared.user_id)
    assert private.results[0].data_id in proposal.delete_data_ids
    reasons = {r["reason"] for r in proposal.retained}
    assert any("shared connection" in r for r in reasons)


async def test_data_published_to_the_org_is_retained(
    pool, queue, blobs, settings, tenant, principal_for
):
    """They chose to publish it to colleagues. Withdrawing it on exit deletes
    something the organisation has been relying on."""
    actor = await principal_for(tenant.api_key)
    published = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                              "published-1", access=ItemAccess(level="org"))
    kept = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "private-1")

    proposal = await account.plan(pool, tenant.org_id, tenant.user_id)
    assert kept.results[0].data_id in proposal.delete_data_ids
    assert published.results[0].data_id not in proposal.delete_data_ids
    assert any("organisation" in r["reason"] for r in proposal.retained)


async def test_a_dry_run_reports_both_counts_with_reasons(
    pool, queue, blobs, settings, tenant, principal_for
):
    """"We deleted 400 records" without "and kept 1,200 that belong to the
    project" is an answer that gets disputed later."""
    actor = await principal_for(tenant.api_key)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "d-1")
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "d-2",
                 access=ItemAccess(level="org"))

    result = await account.delete_account(
        pool, queue, actor, user_id=tenant.user_id, dry_run=True
    )
    assert result["applied"] is False
    assert result["deleting"] == 1 and result["retaining"] == 1
    assert result["retained_by_reason"]
    # Nothing moved.
    assert await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE deleted_at IS NOT NULL"
    ) == 0


async def test_revocation_happens_even_though_erasure_is_asynchronous(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The security half of offboarding must not wait on the retention half."""
    actor = await principal_for(tenant.api_key)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "r-1")

    await account.delete_account(pool, queue, actor, user_id=tenant.user_id)

    assert await pool.fetchval(
        "SELECT count(*) FROM api_keys WHERE user_id = $1 AND revoked_at IS NULL",
        tenant.user_id,
    ) == 0
    assert await pool.fetchval(
        "SELECT status FROM producers WHERE producer_id = $1", tenant.producer_id
    ) == "disabled"
    assert await pool.fetchval(
        "SELECT count(*) FROM memberships WHERE user_id = $1", tenant.user_id
    ) == 0


async def test_the_audit_record_outlives_the_account(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await account.delete_account(pool, queue, actor, user_id=tenant.user_id,
                                  reason="requested by the user")

    row = await pool.fetchrow(
        "SELECT action, detail FROM audit_events WHERE action = 'account.deleted'"
    )
    assert row is not None
    assert row["detail"]["reason"] == "requested by the user"
    assert row["detail"]["requested_by"] == tenant.user_id


async def test_deleting_someone_elses_account_needs_the_capability(
    pool, tenant, queue, principal_for
):
    other_id = await create_user(pool, "colleague-del@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        other_id, tenant.org_id,
    )
    token = await issue_key(pool, user_id=other_id, org_id=tenant.org_id,
                            capabilities=[DATA_READ, DATA_WRITE])
    member = await principal_for(token)

    from open_mem.auth import AuthError

    with pytest.raises((AccountError, AuthError)):
        await account.delete_account(pool, queue, member, user_id=tenant.user_id)


async def test_another_orgs_user_is_not_addressable(pool, queue, tenant, other_tenant, principal_for):
    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(AccountError) as exc:
        await account.delete_account(pool, queue, intruder, user_id=tenant.user_id)
    assert exc.value.status == 404
