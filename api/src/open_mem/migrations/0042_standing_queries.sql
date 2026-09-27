-- W10 — say once what you want to be told about.
--
-- Everything else in the platform answers when asked. This is the one primitive
-- that speaks first, and four published use cases are blocked on it.
--
-- Deliberately its own table rather than an alert with a different surface.
-- Alerts match on an event's payload; this matches on the **item**, and reusing
-- the alert matcher would mean `contains` over a text preview -- no stemming,
-- no phrases, silently missing "Acme's" and matching "acmeism". The plumbing is
-- borrowed and the matcher is not.
CREATE TABLE standing_queries (
    query_id        text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    -- Whose rights a match is checked against at delivery. A match sent to
    -- somebody who cannot see the item is a leak through the notification
    -- channel, so this is not decoration -- it is the control.
    owner_id        text NOT NULL REFERENCES users ON DELETE CASCADE,
    name            text NOT NULL,
    -- `{query, data_type, tags, producer_id}`. The text half runs through
    -- `websearch_to_tsquery`, which is the same lexical engine retrieval uses:
    -- a standing query and a search then agree about what the words mean.
    selector        jsonb NOT NULL DEFAULT '{}',
    -- `poll` records and waits to be asked. `memory` promotes the item into a
    -- named memory, which needs no network, no secret and no retry, and
    -- composes with everything already built.
    delivery        jsonb NOT NULL DEFAULT '{"kind": "poll"}',
    -- The sequence this query has evaluated up to. Advanced only by a completed
    -- evaluation: each item is seen exactly once, and **nothing re-scans**. A
    -- standing query that re-scanned would be a scheduled full-table scan
    -- somebody registers a hundred of.
    watermark       bigint NOT NULL DEFAULT 0,
    batch_cap       int NOT NULL DEFAULT 500,
    config_version  int NOT NULL DEFAULT 1,
    backtested_version int,
    enabled         boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    UNIQUE (project_id, name)
);

CREATE INDEX standing_due ON standing_queries (project_id, watermark)
    WHERE enabled AND deleted_at IS NULL;

-- What each pass did. `deferred` exists for the same reason it does on alerts:
-- a run that silently truncated at its cap reads as "nothing else matched",
-- which for something whose whole job is telling you is the worst lie available.
CREATE TABLE standing_runs (
    run_id          text PRIMARY KEY,
    query_id        text NOT NULL REFERENCES standing_queries ON DELETE CASCADE,
    config_version  int NOT NULL,
    trigger         text NOT NULL CHECK (trigger IN ('tick', 'manual', 'backtest')),
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed')),
    from_sequence   bigint NOT NULL,
    to_sequence     bigint,
    candidates      int NOT NULL DEFAULT 0,
    matches         int NOT NULL DEFAULT 0,
    withheld        int NOT NULL DEFAULT 0,
    deferred        int NOT NULL DEFAULT 0,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX standing_runs_query ON standing_runs (query_id, started_at DESC);

-- One row per item a query caught.
--
-- `visible` records whether the **owner** could see the item when it matched.
-- A withheld match is still recorded, because a feed that silently omits what
-- it could not deliver is incomplete in a way nobody can explain -- and the
-- count is the evidence that the ACL did its job rather than that the query is
-- broken.
CREATE TABLE standing_matches (
    match_id        text PRIMARY KEY,
    query_id        text NOT NULL REFERENCES standing_queries ON DELETE CASCADE,
    run_id          text NOT NULL REFERENCES standing_runs ON DELETE CASCADE,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    sequence        bigint NOT NULL,
    visible         boolean NOT NULL,
    matched_at      timestamptz NOT NULL DEFAULT now(),
    -- An item matching one query twice is a redelivery, not a second match.
    UNIQUE (query_id, data_id)
);

CREATE INDEX standing_matches_feed ON standing_matches (query_id, sequence DESC);
