-- The meter, and the budget it feeds.
--
-- Until now nothing recorded what a model call cost. Every counter in
-- `telemetry.py` is in-process and sampled, which is the right shape for
-- "is it working" and the wrong shape for "who spent this" -- a metric that
-- can be dropped under load cannot be the basis of a bill or a quota.
--
-- Three properties this has to carry, and each of them is a defect in the
-- obvious design:
--
--   * **Failed calls are recorded.** A call that times out after generating
--     three thousand tokens consumed three thousand tokens. Counting only
--     successes under-reports spend systematically, and in exactly the
--     direction that produces a surprise bill.
--   * **Attribution follows the engine that answered**, never the one that was
--     configured. The fallback chain exists so a provider outage degrades
--     quality rather than availability -- but it also means a $0 local call can
--     silently become a paid cloud one. `serving_engine` and `crossed_to_paid`
--     are what make that visible.
--   * **Input, output and cached tokens are separate columns.** Providers price
--     them several times apart, so a single `tokens` total cannot produce a
--     cost, only a number that looks like one.
--
-- Volume is the reason for the second table. One row per inference call at
-- ingest rates is millions of rows, so enforcement does not read them: it reads
-- `usage_spend`, which is one row per (scope, day). Raw events are for dispute
-- and debugging and have a short retention; the rollup is small and kept.

CREATE TABLE usage_events (
    usage_id        text PRIMARY KEY,
    -- Monotonic per deployment, for the same reason `domain_events` has one:
    -- ordering by a generated id would be nearly right, and billing is not a
    -- place for nearly.
    sequence        bigserial NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now(),

    -- Who. `org_id` is the only one guaranteed present: a crawl tick or a
    -- reconcile has no user, and refusing to meter unattended work would leave
    -- exactly the spend nobody is watching unaccounted.
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text REFERENCES projects ON DELETE SET NULL,
    user_id         text REFERENCES users ON DELETE SET NULL,

    -- What caused it. Each of these is a thing that quoted an estimate
    -- somewhere -- a dry-run projection, a staleness rebuild preview -- and an
    -- estimate never reconciled against an actual drifts until nobody trusts
    -- it, at which point the gate it guards becomes a formality.
    run_id          text,
    data_id         text,
    case_id         text,

    purpose         text NOT NULL,

    -- Asked for versus answered. Cost attribution uses the second.
    configured_model text,
    serving_engine  text NOT NULL,
    serving_model   text,
    fallback_depth  int NOT NULL DEFAULT 0,
    -- A fallback that moved the call from a free engine to a paid one. Its own
    -- flag rather than a `fallback_depth > 0` inference, because it is a
    -- category change -- money where there was none -- rather than a
    -- degradation, and the two want different alerts.
    crossed_to_paid boolean NOT NULL DEFAULT false,

    -- Whose money. Three economies share this code path and conflating them
    -- produces bad enforcement decisions: a user's own provider key is their
    -- spend to see, not ours to cap.
    billing_account text NOT NULL DEFAULT 'platform'
                    CHECK (billing_account IN ('user_key', 'org_key', 'platform')),

    tokens_in       bigint NOT NULL DEFAULT 0,
    tokens_out      bigint NOT NULL DEFAULT 0,
    tokens_cached   bigint NOT NULL DEFAULT 0,

    -- The cost-weighted unit budgets are denominated in. Not currency: rating
    -- against a rate card is a later concern and needs a `rate_card_version` to
    -- be reproducible. Credits are the thing that has to exist now, because a
    -- budget needs something to decrement today.
    credits         bigint NOT NULL DEFAULT 0,

    latency_ms      int,
    status          text NOT NULL
                    CHECK (status IN ('ok', 'failed', 'timeout', 'refused', 'skipped')),
    detail          jsonb NOT NULL DEFAULT '{}'
);

-- The investigation query: one org's spend over a window, newest first.
CREATE INDEX ON usage_events (org_id, occurred_at DESC);
-- The reconciliation query: what did this run actually cost, against what its
-- dry-run promised.
CREATE INDEX ON usage_events (run_id) WHERE run_id IS NOT NULL;
-- Finding the crossings is the point of recording them.
CREATE INDEX ON usage_events (org_id, occurred_at DESC) WHERE crossed_to_paid;


-- The enforcement read. Deliberately denormalised and incremented in the same
-- transaction as the event, so a budget check is one indexed row rather than an
-- aggregate over a table that grows with ingest.
--
-- Three scopes rather than one because a budget is set at three levels and each
-- needs its own running total. A project that has exhausted its own budget must
-- not be able to spend the org's remainder.
CREATE TABLE usage_spend (
    scope       text NOT NULL CHECK (scope IN ('org', 'project', 'user')),
    scope_id    text NOT NULL,
    day         date NOT NULL,
    credits     bigint NOT NULL DEFAULT 0,
    events      bigint NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_id, day)
);

-- No foreign key to organizations/projects/users on purpose: the rollup is the
-- record of what was spent, and it has to survive the deletion of the thing
-- that spent it. An org erased mid-month still owes the month.
CREATE INDEX ON usage_spend (day);
