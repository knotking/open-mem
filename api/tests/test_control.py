"""The control plane.

Most of these test a boundary rather than a feature. The features are CRUD; the
boundaries are what stop the control plane becoming a privilege-escalation
surface.
"""

from __future__ import annotations

import pytest

from memdog import control
from memdog.auth import ADMIN, CONFIG_WRITE, DATA_READ, DATA_WRITE, AuthError, issue_key
from memdog.bootstrap import create_user
from memdog.control import ControlError

pytestmark = pytest.mark.asyncio


async def _member(pool, tenant, principal_for, role="member", capabilities=None):
    user_id = await create_user(pool, f"{role}-{len(role)}-{id(pool)}@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, $3)",
        user_id, tenant.org_id, role,
    )
    token = await issue_key(
        pool, user_id=user_id, org_id=tenant.org_id,
        project_id=tenant.project_id,
        capabilities=capabilities or [DATA_READ, DATA_WRITE, CONFIG_WRITE],
    )
    return user_id, await principal_for(token)


async def test_a_key_cannot_grant_more_than_the_credential_that_made_it(
    pool, tenant, principal_for
):
    """Otherwise capability scoping is decorative: any data:read key could mint
    itself an admin one."""
    _, member = await _member(pool, tenant, principal_for, capabilities=[DATA_READ])
    with pytest.raises(ControlError) as exc:
        await control.create_key(pool, member, name="escalate", capabilities=[ADMIN])
    assert exc.value.status == 403

    issued = await control.create_key(pool, member, name="fine", capabilities=[DATA_READ])
    assert issued["token"].startswith("mdk_")
    # Listing never re-displays the secret.
    keys = await control.list_keys(pool, member)
    assert all("token" not in k for k in keys)
    assert any(k["prefix"] == issued["prefix"] for k in keys)


async def test_a_member_cannot_add_members_or_create_projects(pool, tenant, principal_for):
    _, member = await _member(pool, tenant, principal_for, role="member")
    for call in (
        lambda: control.add_member(pool, member, email="x@example.com", role="admin"),
        lambda: control.create_project(pool, member, name="theirs"),
    ):
        with pytest.raises(ControlError) as exc:
            await call()
        assert exc.value.status == 403


async def test_removing_a_member_revokes_their_keys(pool, tenant, principal_for):
    """Leaving live keys on a departed member is the offboarding leak."""
    owner = await principal_for(tenant.api_key)
    user_id, member = await _member(pool, tenant, principal_for)
    assert await control.list_keys(pool, member)

    result = await control.remove_member(pool, owner, user_id)
    assert result["keys_revoked"] >= 1

    # The credential stops working immediately, not at next rotation.
    row = await pool.fetchrow("SELECT prefix FROM api_keys WHERE user_id = $1", user_id)
    from memdog.auth import ApiKeyVerifier
    assert row is not None
    revoked = await pool.fetchval(
        "SELECT revoked_at IS NOT NULL FROM api_keys WHERE user_id = $1", user_id
    )
    assert revoked


async def test_an_admin_cannot_remove_themselves(pool, tenant, principal_for):
    """A locked-out organization needs a support ticket to recover."""
    owner = await principal_for(tenant.api_key)
    with pytest.raises(ControlError) as exc:
        await control.remove_member(pool, owner, tenant.user_id)
    assert exc.value.status == 409


async def test_another_orgs_resources_are_not_addressable(
    pool, tenant, other_tenant, principal_for
):
    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(ControlError) as exc:
        await control.create_producer(
            pool, intruder, project_id=tenant.project_id, producer_type="client"
        )
    assert exc.value.status == 404


async def test_a_group_cannot_contain_a_non_member(pool, tenant, principal_for):
    """A group is a principal for sharing, so a non-member in one is a grant to
    an outsider."""
    owner = await principal_for(tenant.api_key)
    group = await control.create_group(pool, owner, name="engineering")
    outsider = await create_user(pool, "outsider-group@example.com")
    member_id, _ = await _member(pool, tenant, principal_for)

    result = await control.set_group_members(pool, owner, group["group_id"],
                                              [member_id, outsider])
    assert result["members"] == [member_id]


async def test_a_new_project_can_route_its_first_write(pool, tenant, principal_for):
    """A project without memory types cannot route anything, so creation ships
    them."""
    owner = await principal_for(tenant.api_key)
    project = await control.create_project(pool, owner, name="second")
    types = await pool.fetch(
        "SELECT name FROM memory_types WHERE project_id = $1", project["project_id"]
    )
    assert "default" in {t["name"] for t in types}


async def test_producer_freshness_is_reported(pool, tenant, queue, blobs, settings, principal_for):
    """The highest-value detector: it catches a stopped webhook, a broken
    crawler selector and a dead client with one query."""
    from memdog.contracts import Inline, WriteItem, WriteRequest
    from memdog.write import write_items

    owner = await principal_for(tenant.api_key)
    before = {p["producer_id"]: p for p in await control.list_producers(pool, owner)}
    assert before[tenant.producer_id]["seconds_since_last_item"] is None

    await write_items(
        pool, queue, blobs, settings, owner,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="fresh-1", content=Inline(text="something")),
        ]),
    )
    after = {p["producer_id"]: p for p in await control.list_producers(pool, owner)}
    assert after[tenant.producer_id]["seconds_since_last_item"] is not None


async def test_changing_a_connection_scope_does_not_rewrite_history(
    pool, tenant, queue, blobs, settings, principal_for
):
    """Those items were assigned an ACL at write time; silently re-filing them
    would change who can see existing data."""
    from memdog.contracts import Inline, WriteItem, WriteRequest
    from memdog.write import write_items

    owner = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, owner,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="scoped-1", content=Inline(text="written while personal")),
        ]),
    )
    before = await pool.fetchval(
        "SELECT access_level FROM data_items WHERE data_id = $1", written.results[0].data_id
    )
    result = await control.set_connection_scope(pool, owner, tenant.connection_id, "shared")
    assert result["applies_to"] == "future writes only"

    after = await pool.fetchval(
        "SELECT access_level FROM data_items WHERE data_id = $1", written.results[0].data_id
    )
    assert after == before == "private"


async def test_membership_is_not_discoverable_by_probing(pool, other_tenant, principal_for):
    """An org you are not in answers the same as one that does not exist."""
    intruder = await principal_for(other_tenant.api_key)
    from memdog.auth import Principal

    elsewhere = Principal(
        user_id=intruder.user_id, org_id="org_does_not_exist",
        capabilities=intruder.capabilities,
    )
    with pytest.raises(ControlError) as exc:
        await control.list_projects(pool, elsewhere)
    assert exc.value.status == 404
