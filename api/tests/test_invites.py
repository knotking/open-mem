"""Invites, and the registration posture they exist to make workable.

The interesting assertions here are not "an invite works". They are the ones
about what an invite refuses to do: be redeemed twice, be forwarded, outlive its
expiry, or tell a stranger anything about the organization behind it.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from open_mem import invites
from open_mem.auth import ApiKeyVerifier, AuthError
from open_mem.bootstrap import AlreadyBootstrapped, bootstrap_tenant, refuse_if_occupied
from open_mem.invites import INVALID, InviteError
from open_mem.settings_store import put

pytestmark = pytest.mark.asyncio


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def test_the_default_posture_is_closed(pool, tenant):
    """Shipping open and closing later leaves everyone who signed up in between
    already inside, and closing does not remove them."""
    assert await invites.registration_mode(pool, tenant.org_id) == "invite_only"


async def test_an_invite_is_redeemed_into_a_membership(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(
        pool, actor, email="Newcomer@Acme.example", role="member"
    )
    # Normalised on the way in, so a capitalised address in the invite and a
    # lowercase one at redemption are the same person.
    assert created.email == "newcomer@acme.example"

    redeemed = await invites.redeem(
        pool, token=created.token, email="newcomer@acme.example"
    )
    assert redeemed.org_id == tenant.org_id
    assert redeemed.role == "member"

    role = await pool.fetchval(
        "SELECT role FROM memberships WHERE user_id = $1 AND org_id = $2",
        redeemed.user_id, tenant.org_id,
    )
    assert role == "member"

    # The key it hands back is real, and carries what the role implies -- no
    # more. A redeemer who arrived as a viewer must not hold config:write.
    verified = await ApiKeyVerifier(pool).verify(redeemed.api_key)
    assert verified.user_id == redeemed.user_id
    assert verified.can("data:write")
    assert not verified.can("config:write")


async def test_a_viewer_invite_grants_only_reading(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(
        pool, actor, email="readonly@acme.example", role="viewer"
    )
    redeemed = await invites.redeem(
        pool, token=created.token, email="readonly@acme.example"
    )
    verified = await ApiKeyVerifier(pool).verify(redeemed.api_key)
    assert verified.can("data:read")
    assert not verified.can("data:write")


async def test_an_invite_is_single_use(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="once@acme.example")

    await invites.redeem(pool, token=created.token, email="once@acme.example")
    with pytest.raises(InviteError) as exc:
        await invites.redeem(pool, token=created.token, email="once@acme.example")
    assert str(exc.value) == INVALID


async def test_two_redemptions_racing_produce_one_member(pool, tenant):
    """The check-then-write version passes both reads and creates two members.
    The conditional update is what makes single-use true under contention."""
    actor = await _principal(pool, tenant)
    created = await invites.create(
        pool, actor, email="race@acme.example", transferable=False
    )

    results = await asyncio.gather(
        invites.redeem(pool, token=created.token, email="race@acme.example"),
        invites.redeem(pool, token=created.token, email="race@acme.example"),
        return_exceptions=True,
    )
    succeeded = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, InviteError)]
    assert len(succeeded) == 1
    assert len(refused) == 1

    members = await pool.fetchval(
        "SELECT count(*) FROM memberships WHERE org_id = $1", tenant.org_id
    )
    assert members == 2   # the bootstrap owner, and exactly one newcomer


async def test_a_forwarded_invite_fails(pool, tenant):
    """An unscoped link is transferable by design; a bound one is not, which is
    the behaviour the person sending it intended."""
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="intended@acme.example")

    with pytest.raises(InviteError) as exc:
        await invites.redeem(
            pool, token=created.token, email="someone.else@acme.example"
        )
    assert str(exc.value) == INVALID
    # And the invite survives the attempt, so the intended recipient can still
    # use it. A failed forward must not burn the invite.
    ok = await invites.redeem(
        pool, token=created.token, email="intended@acme.example"
    )
    assert ok.role == "member"


async def test_binding_to_an_address_is_the_default(pool, tenant):
    actor = await _principal(pool, tenant)
    with pytest.raises(InviteError) as exc:
        await invites.create(pool, actor, email=None)
    assert "transferable=true" in str(exc.value)

    # Opting out is explicit, and then anyone holding it may redeem.
    link = await invites.create(pool, actor, transferable=True)
    assert link.email is None
    redeemed = await invites.redeem(
        pool, token=link.token, email="whoever@acme.example"
    )
    assert redeemed.role == "member"


async def test_an_expired_invite_is_refused(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="late@acme.example")
    await pool.execute(
        "UPDATE invites SET expires_at = now() - interval '1 day' WHERE invite_id = $1",
        created.invite_id,
    )
    with pytest.raises(InviteError) as exc:
        await invites.redeem(pool, token=created.token, email="late@acme.example")
    assert str(exc.value) == INVALID


async def test_a_revoked_invite_is_refused(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="revoked@acme.example")
    await invites.revoke(pool, actor, created.invite_id)

    with pytest.raises(InviteError) as exc:
        await invites.redeem(pool, token=created.token, email="revoked@acme.example")
    assert str(exc.value) == INVALID


async def test_redemption_discloses_nothing(pool, tenant):
    """Anything that separates "no such organization" from "wrong token for
    this one" makes the endpoint an org-enumeration oracle."""
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="someone@acme.example")

    attempts = [
        ("mdi_neverexisted.aaaaaaaaaaaaaaaaaaaaaaaa", "someone@acme.example"),
        (created.prefix + ".wrongsecretwrongsecretwrong", "someone@acme.example"),
        (created.token, "wrong.person@acme.example"),
        ("not-even-shaped-like-a-token", None),
    ]
    messages = set()
    statuses = set()
    for token, email in attempts:
        with pytest.raises(InviteError) as exc:
            await invites.redeem(pool, token=token, email=email)
        messages.add(str(exc.value))
        statuses.add(exc.value.status)
    assert messages == {INVALID}
    assert statuses == {403}


async def test_a_revoke_from_another_org_is_not_an_existence_oracle(
    pool, tenant, other_tenant
):
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="theirs@acme.example")

    intruder = await _principal(pool, other_tenant)
    with pytest.raises(InviteError) as exc:
        await invites.revoke(pool, intruder, created.invite_id)
    # The same 404 an invented id gets.
    assert exc.value.status == 404
    with pytest.raises(InviteError) as invented:
        await invites.revoke(pool, intruder, "inv_doesnotexist")
    assert invented.value.status == 404


async def test_only_an_admin_may_invite(pool, tenant):
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="member@acme.example")
    redeemed = await invites.redeem(
        pool, token=created.token, email="member@acme.example"
    )
    ordinary = await ApiKeyVerifier(pool).verify(redeemed.api_key)

    with pytest.raises(AuthError) as exc:
        await invites.create(pool, ordinary, email="another@acme.example")
    assert exc.value.status == 403


async def test_the_role_travels_on_the_invite(pool, tenant):
    """A redeemer who could name their own role would be an unauthenticated
    privilege escalation, so redemption takes no role at all."""
    import inspect

    assert "role" not in inspect.signature(invites.redeem).parameters


async def test_invites_are_audited_on_creation_and_on_redemption(pool, tenant):
    """Who invited them and who walked through the door are different
    questions, and a forwarded invite answers only the second."""
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="audited@acme.example")
    redeemed = await invites.redeem(
        pool, token=created.token, email="audited@acme.example"
    )

    rows = await pool.fetch(
        "SELECT action, actor_user_id, detail FROM audit_events "
        "WHERE target_id = $1 ORDER BY at",
        created.invite_id,
    )
    actions = [r["action"] for r in rows]
    assert actions == ["invite.created", "invite.redeemed"]
    assert rows[0]["actor_user_id"] == tenant.user_id
    assert rows[1]["actor_user_id"] == redeemed.user_id
    # The token appears in neither record.
    assert created.token not in str(rows[0]["detail"]) + str(rows[1]["detail"])


async def test_a_stored_invite_is_not_a_usable_credential(pool, tenant):
    """Hashed like an API key, because it is one: a database read must not yield
    something that can be redeemed."""
    actor = await _principal(pool, tenant)
    created = await invites.create(pool, actor, email="hashed@acme.example")

    row = await pool.fetchrow(
        "SELECT prefix, token_hash FROM invites WHERE invite_id = $1",
        created.invite_id,
    )
    assert created.token not in str(bytes(row["token_hash"]))
    assert row["prefix"] == created.prefix
    listed = await invites.listing(pool, actor)
    assert all("token" not in entry for entry in listed)


async def test_disabled_registration_refuses_to_issue_an_invite(pool, tenant):
    """An invite that cannot be redeemed is worse than none: somebody sends it
    and waits."""
    actor = await _principal(pool, tenant)
    await put(pool, "registration_mode", "disabled", scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)

    with pytest.raises(InviteError) as exc:
        await invites.create(pool, actor, email="nope@acme.example")
    assert exc.value.status == 409


async def test_pending_for_email_is_what_registration_consults(pool, tenant):
    actor = await _principal(pool, tenant)
    assert not await invites.pending_for_email(pool, "nobody@acme.example")
    assert not await invites.pending_for_email(pool, None)

    created = await invites.create(pool, actor, email="expected@acme.example")
    assert await invites.pending_for_email(pool, "EXPECTED@acme.example")

    await invites.revoke(pool, actor, created.invite_id)
    assert not await invites.pending_for_email(pool, "expected@acme.example")


async def test_listing_reports_state_rather_than_only_rows(pool, tenant):
    actor = await _principal(pool, tenant)
    live = await invites.create(pool, actor, email="live@acme.example")
    killed = await invites.create(pool, actor, email="killed@acme.example")
    await invites.revoke(pool, actor, killed.invite_id)
    stale = await invites.create(pool, actor, email="stale@acme.example")
    await pool.execute(
        "UPDATE invites SET expires_at = now() - interval '1 day' WHERE invite_id = $1",
        stale.invite_id,
    )

    by_id = {e["invite_id"]: e["status"] for e in await invites.listing(pool, actor)}
    assert by_id[live.invite_id] == "pending"
    assert by_id[killed.invite_id] == "revoked"
    assert by_id[stale.invite_id] == "expired"


async def test_bootstrap_refuses_once_anybody_exists(pool):
    """The chicken-and-egg exception has to stay an exception. One that can be
    taken twice is an unauthenticated account-creation endpoint."""
    await refuse_if_occupied(pool)          # empty deployment: permitted
    await bootstrap_tenant(pool, org_name="first", email="first@acme.example")

    with pytest.raises(AlreadyBootstrapped) as exc:
        await refuse_if_occupied(pool)
    assert "invite" in str(exc.value)


# --- the HTTP surface --------------------------------------------------------


@pytest.fixture
async def client(pool, tenant):
    from open_mem.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


async def test_the_invite_round_trip_over_http(client, tenant, pool):
    auth = {"Authorization": f"Bearer {tenant.api_key}"}

    created = await client.post(
        "/api/v1/invites", headers=auth,
        json={"email": "http@acme.example", "role": "member"},
    )
    assert created.status_code == 200
    token = created.json()["token"]

    listed = await client.get("/api/v1/invites", headers=auth)
    assert listed.json()["invites"][0]["status"] == "pending"

    # Redemption carries no credential of its own: the invite is the credential,
    # and the person holding it has no account yet.
    redeemed = await client.post(
        "/api/v1/invites/redeem",
        json={"token": token, "email": "http@acme.example"},
    )
    assert redeemed.status_code == 200
    new_key = redeemed.json()["api_key"]

    # And the key works against an ordinary endpoint.
    mine = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {new_key}"}
    )
    assert mine.status_code == 200


async def test_a_bad_redemption_is_403_with_nothing_in_it(client):
    response = await client.post(
        "/api/v1/invites/redeem",
        json={"token": "mdi_nope.aaaaaaaaaaaaaaaaaaaa", "email": "x@acme.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == INVALID


async def test_redeeming_is_rate_limited(client, tenant, pool):
    """Unauthenticated and bearer-token-shaped is what people brute-force."""
    await put(pool, "rate_limit_credits_per_minute", 300, scope="platform",
              scope_id=None, set_by=tenant.user_id)

    seen = []
    for _ in range(6):
        response = await client.post(
            "/api/v1/invites/redeem",
            json={"token": "mdi_guessing.aaaaaaaaaaaaaaaaaaaa",
                  "email": "x@acme.example"},
        )
        seen.append(response.status_code)
    assert 429 in seen
    assert seen[0] == 403        # the first few are ordinary refusals


async def test_the_limiter_cannot_be_escaped_by_varying_the_token(client, tenant, pool):
    """Keying on anything the caller supplies hands them the bucket: change the
    prefix, get a fresh allowance, and the limiter limits nothing."""
    await put(pool, "rate_limit_credits_per_minute", 300, scope="platform",
              scope_id=None, set_by=tenant.user_id)

    seen = []
    for n in range(6):
        response = await client.post(
            "/api/v1/invites/redeem",
            json={"token": f"mdi_guess{n:05d}.aaaaaaaaaaaaaaaaaaaa",
                  "email": "x@acme.example"},
        )
        seen.append(response.status_code)
    assert 429 in seen
