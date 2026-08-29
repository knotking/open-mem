"""Quota, and the reason it is not a request counter.

The property under test throughout is that cost, not arrival, is what is
limited -- and that the two mechanisms stay separate: the bucket protects the
instance from a burst, the budget protects money from a month.
"""

from __future__ import annotations

import pytest

from memdog import quota, usage
from memdog.settings_store import put

# No module-level asyncio mark: `asyncio_mode = "auto"` already runs the async
# tests, and half of these are ordinary functions -- the pricing rules are pure
# arithmetic and want no database to check.


def test_a_generation_costs_orders_of_magnitude_more_than_a_search():
    """The whole argument against counting requests, as an assertion."""
    search = quota.estimate_retrieve(match=["vector"], limit=20)
    hybrid = quota.estimate_retrieve(match=["vector", "lexical"], limit=20)
    generation = quota.estimate_ask(match=["vector", "lexical"], passages=8)

    assert search == 1
    assert hybrid == 2
    # A request counter sees three identical requests here.
    assert generation > 100 * search


def test_the_bucket_refuses_on_cost_not_on_count():
    bucket = quota.Bucket()

    # A hundred cheap requests fit inside a budget that two expensive ones
    # would exhaust.
    for _ in range(100):
        bucket.charge("key-a", 1, per_minute=200)

    bucket.charge("key-b", 100, per_minute=200)
    with pytest.raises(quota.QuotaExceeded) as exc:
        bucket.charge("key-b", 150, per_minute=200)
    assert exc.value.retry_after >= 1


def test_the_bucket_drains_rather_than_resetting_on_a_boundary(monkeypatch):
    """A fixed window hands out two full allowances to a caller who straddles
    it. Continuous refill is what closes that."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(quota.time, "monotonic", lambda: clock["t"])
    bucket = quota.Bucket()

    bucket.charge("key", 60, per_minute=60)
    with pytest.raises(quota.QuotaExceeded):
        bucket.charge("key", 1, per_minute=60)

    clock["t"] += 30          # half a minute of refill at 60/minute
    bucket.charge("key", 30, per_minute=60)
    with pytest.raises(quota.QuotaExceeded):
        bucket.charge("key", 1, per_minute=60)


def test_a_zero_limit_means_unlimited_not_refuse_everything():
    bucket = quota.Bucket()
    bucket.charge("key", 10_000, per_minute=0)


def test_concurrency_is_bounded_per_key():
    """A rate limit bounds arrival; it does not stop one client occupying the
    whole model tier with slow requests."""
    concurrency = quota.Concurrency()
    concurrency.acquire("key", limit=2)
    concurrency.acquire("key", limit=2)
    with pytest.raises(quota.QuotaExceeded):
        concurrency.acquire("key", limit=2)

    concurrency.release("key")
    concurrency.acquire("key", limit=2)
    # A different credential is unaffected.
    concurrency.acquire("other", limit=2)


async def test_no_ceiling_anywhere_means_no_refusal(pool, tenant):
    await quota.check_budget(pool, org_id=tenant.org_id,
                             project_id=tenant.project_id, cost=10_000)


async def test_an_exhausted_org_budget_refuses(pool, tenant):
    await put(pool, "budget_daily_credits", 100, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await _spend(pool, "org", tenant.org_id, 100)

    with pytest.raises(quota.BudgetExhausted) as exc:
        await quota.check_budget(pool, org_id=tenant.org_id)
    assert exc.value.scope == "org"
    # A day, not a minute. A client that cannot tell a budget from a rate limit
    # retries this every minute until midnight.
    assert exc.value.retry_after > 60


async def test_a_project_cannot_raise_the_ceiling_its_org_set(pool, tenant):
    """`settings_store` resolves most-specific-wins, which is right for a
    preference and wrong for a cap. A ceiling composes as a minimum."""
    await put(pool, "budget_daily_credits", 10, scope="platform", scope_id=None,
              set_by=tenant.user_id, org_id=tenant.org_id)
    await put(pool, "budget_daily_credits", 100_000, scope="project",
              scope_id=tenant.project_id, set_by=tenant.user_id,
              org_id=tenant.org_id, project_id=tenant.project_id)
    await _spend(pool, "project", tenant.project_id, 50)

    with pytest.raises(quota.BudgetExhausted) as exc:
        await quota.check_budget(pool, org_id=tenant.org_id,
                                 project_id=tenant.project_id)
    assert exc.value.scope == "project"


async def test_each_scope_binds_independently(pool, tenant):
    """A project out of budget is refused even with org headroom to spare."""
    await put(pool, "budget_daily_credits", 10_000, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await put(pool, "budget_daily_credits", 50, scope="project",
              scope_id=tenant.project_id, set_by=tenant.user_id,
              org_id=tenant.org_id, project_id=tenant.project_id)
    await _spend(pool, "org", tenant.org_id, 60)
    await _spend(pool, "project", tenant.project_id, 60)

    with pytest.raises(quota.BudgetExhausted) as exc:
        await quota.check_budget(pool, org_id=tenant.org_id,
                                 project_id=tenant.project_id)
    assert exc.value.scope == "project"


async def test_the_meter_decrements_the_budget_the_estimate_did_not(pool, tenant):
    """The estimate charges the bucket, the actual charges the budget. Charging
    both to the budget would bill every request twice."""
    usage.configure(pool)
    try:
        await put(pool, "budget_daily_credits", 6, scope="org",
                  scope_id=tenant.org_id, set_by=tenant.user_id,
                  org_id=tenant.org_id)

        await quota.check_budget(pool, org_id=tenant.org_id)

        with usage.attributed(org_id=tenant.org_id):
            async with usage.meter("answer", "gemini", model_id="g"):
                usage.observe(tokens_in=1000, tokens_out=1000)   # 5 credits

        # Still under, by one.
        await quota.check_budget(pool, org_id=tenant.org_id)
        with pytest.raises(quota.BudgetExhausted):
            await quota.check_budget(pool, org_id=tenant.org_id, cost=2)
    finally:
        usage.reset()


async def test_a_user_cannot_raise_the_ceiling_set_above_them(pool, tenant):
    """The register puts a budget at user scope -- one person's sandbox must
    not spend the team's month -- which is only safe because caps compose as a
    minimum. Precedence would let the person being limited lift their limit."""
    await put(pool, "budget_daily_credits", 20, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await put(pool, "budget_daily_credits", 1_000_000, scope="user",
              scope_id=tenant.user_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await _spend(pool, "user", tenant.user_id, 25)

    with pytest.raises(quota.BudgetExhausted) as exc:
        await quota.check_budget(pool, org_id=tenant.org_id,
                                 user_id=tenant.user_id)
    assert exc.value.scope == "user"


async def test_a_user_may_hold_themselves_below_the_org_ceiling(pool, tenant):
    await put(pool, "budget_daily_credits", 10_000, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await put(pool, "budget_daily_credits", 10, scope="user",
              scope_id=tenant.user_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await _spend(pool, "user", tenant.user_id, 10)

    with pytest.raises(quota.BudgetExhausted):
        await quota.check_budget(pool, org_id=tenant.org_id,
                                 user_id=tenant.user_id)
    # The org itself is nowhere near its ceiling.
    await quota.check_budget(pool, org_id=tenant.org_id)


async def test_spend_today_reports_the_ceiling_that_binds(pool, tenant):
    await put(pool, "budget_daily_credits", 100, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    await _spend(pool, "org", tenant.org_id, 100)

    spend = await quota.spend_today(pool, org_id=tenant.org_id,
                                    project_id=tenant.project_id)
    by_scope = {s.scope: s for s in spend}
    assert by_scope["org"].credits == 100
    assert by_scope["org"].limit == 100
    assert by_scope["org"].exhausted
    assert not by_scope["project"].exhausted


def test_a_budget_refusal_is_a_capacity_failure_not_a_bad_message():
    """A worker that hit this must put the message back rather than burn a
    retry: the message is not faulty, the window is."""
    from memdog.queue import _is_capacity_failure

    assert _is_capacity_failure(quota.BudgetExhausted("spent", scope="org"))


def test_tokens_never_round_down_to_free():
    """A thousand calls that each round to zero are a thousand calls that cost
    money and metered as free."""
    assert quota.credits_for_tokens(
        "answer", tokens_in=1, tokens_out=0, tokens_cached=0
    ) == 1
    # Nothing consumed is genuinely nothing, though.
    assert quota.credits_for_tokens(
        "answer", tokens_in=0, tokens_out=0, tokens_cached=0
    ) == 0


async def _spend(pool, scope, scope_id, credits):
    await pool.execute(
        """
        INSERT INTO usage_spend (scope, scope_id, day, credits, events)
        VALUES ($1, $2, CURRENT_DATE, $3, 1)
        ON CONFLICT (scope, scope_id, day) DO UPDATE
            SET credits = usage_spend.credits + EXCLUDED.credits
        """,
        scope, scope_id, credits,
    )


# --- the HTTP surface --------------------------------------------------------


@pytest.fixture
async def client(pool, tenant):
    import httpx

    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_a_burst_is_refused_with_a_retry_after(client, pool, tenant):
    """The refusal has to carry the header, or the client's only option is to
    guess -- and clients that guess retry immediately."""
    await put(pool, "rate_limit_credits_per_minute", 2, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    auth = {"Authorization": f"Bearer {tenant.api_key}"}
    body = {
        "query": "anything",
        "filter": {"project_id": tenant.project_id},
        "match": ["vector", "lexical"],
        "limit": 20,
    }

    first = await client.post("/api/v1/retrieve", headers=auth, json=body)
    assert first.status_code == 200

    second = await client.post("/api/v1/retrieve", headers=auth, json=body)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


async def test_the_usage_surface_reports_spend_against_the_ceiling(client, pool, tenant):
    """A spending control nobody can see is a spending control nobody
    trusts."""
    await put(pool, "budget_daily_credits", 500, scope="org",
              scope_id=tenant.org_id, set_by=tenant.user_id, org_id=tenant.org_id)
    # The app's own lifespan has already configured the meter against the same
    # database; pointing it at the test pool keeps the write on one connection
    # set. Not reset afterwards, deliberately -- the running app is still
    # inside its lifespan and would be left without a meter.
    usage.configure(pool)
    with usage.attributed(org_id=tenant.org_id, project_id=tenant.project_id,
                          user_id=tenant.user_id):
        async with usage.meter("answer", "gemini", model_id="g"):
            usage.observe(tokens_in=1000, tokens_out=1000)

    response = await client.get(
        "/api/v1/usage", headers={"Authorization": f"Bearer {tenant.api_key}"}
    )
    assert response.status_code == 200
    payload = response.json()
    org = next(b for b in payload["budgets"] if b["scope"] == "org")
    assert (org["credits"], org["limit"], org["exhausted"]) == (5, 500, False)
    assert payload["by_engine"][0]["serving_engine"] == "gemini"
    assert payload["by_engine"][0]["credits"] == 5
