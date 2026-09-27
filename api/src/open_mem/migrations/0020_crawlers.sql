-- Crawlers: the ingestion path for data that does not announce itself.
--
-- A backfill and a poll are two schedules of the same thing, so there is one
-- worker with several discovery strategies rather than a class per case.

CREATE TABLE crawlers (
    crawler_id      text PRIMARY KEY,
    -- Every crawler is a registered producer, so admission control, ACL
    -- derivation and freshness detection are the ones that already exist
    -- rather than a parallel set for crawled data.
    producer_id     text NOT NULL REFERENCES producers ON DELETE CASCADE,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    name            text NOT NULL,
    strategy        text NOT NULL CHECK (strategy IN ('http', 'feed', 'traverse')),
    config          jsonb NOT NULL,
    -- Bumped on every edit to scope, strategy or request shape. The dry-run
    -- gate compares against it, so editing a crawler invalidates its approval
    -- rather than silently carrying it forward.
    config_version  int NOT NULL DEFAULT 1,
    dry_run_version int,                    -- config_version of the last good dry run
    enabled         boolean NOT NULL DEFAULT false,
    -- Advanced ONLY when a run completes. A partial run that advanced its
    -- watermark would step over records it never processed, and nothing would
    -- ever come back for them.
    watermark       text,
    schedule        jsonb NOT NULL DEFAULT '{"type": "manual"}',
    next_due_at     timestamptz,
    -- 'skip' means a tick that lands while the previous run is live is recorded
    -- as skipped rather than queued: queueing guarantees an overlap backlog for
    -- any crawl that runs longer than its interval.
    overlap         text NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip', 'queue')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON crawlers (project_id);
CREATE INDEX ON crawlers (next_due_at) WHERE enabled;

CREATE TABLE crawl_runs (
    run_id          text PRIMARY KEY,
    crawler_id      text NOT NULL REFERENCES crawlers ON DELETE CASCADE,
    org_id          text NOT NULL,
    -- A dry run enumerates and reports; it fetches nothing, writes nothing and
    -- spends nothing. Same code path, so what it reports is what a live run
    -- would actually do.
    mode            text NOT NULL DEFAULT 'live' CHECK (mode IN ('dry', 'live')),
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'paused', 'completed',
                                      'partial', 'failed', 'cancelled', 'interrupted')),
    -- The config as it was when the run started. A six-hour job must not
    -- change shape halfway through because someone edited it.
    pinned_config   jsonb NOT NULL,
    pinned_version  int NOT NULL,
    watermark_before text,
    watermark_after text,
    -- Cursor plus discovery position. A crash resumes here rather than
    -- restarting, which is the difference between a three-day backfill being
    -- survivable and not.
    checkpoint      jsonb NOT NULL DEFAULT '{}',
    discovered      int NOT NULL DEFAULT 0,
    emitted         int NOT NULL DEFAULT 0,
    skipped         int NOT NULL DEFAULT 0,
    failed          int NOT NULL DEFAULT 0,
    bytes_seen      bigint NOT NULL DEFAULT 0,
    reason          text,
    -- A worker that dies leaves the run 'running' forever. The reaper looks at
    -- this, not at the queue.
    heartbeat_at    timestamptz,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX ON crawl_runs (crawler_id, started_at DESC);
CREATE INDEX ON crawl_runs (status) WHERE status IN ('running', 'pending');

CREATE TABLE crawl_frontier (
    run_id          text NOT NULL REFERENCES crawl_runs ON DELETE CASCADE,
    external_id     text NOT NULL,
    url             text,
    depth           int NOT NULL DEFAULT 0,
    payload         jsonb,
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'in_flight', 'done', 'error', 'skipped')),
    PRIMARY KEY (run_id, external_id)
);

CREATE INDEX ON crawl_frontier (run_id, status);

-- Change detection. Without it a nightly crawl of 50,000 records re-embeds
-- 50,000 records every night, and the enrichment bill is the whole corpus
-- rather than the delta.
CREATE TABLE crawl_seen (
    crawler_id      text NOT NULL REFERENCES crawlers ON DELETE CASCADE,
    external_id     text NOT NULL,
    -- Whichever of version field, etag or content hash the source offers.
    version_hash    text NOT NULL,
    last_run_id     text,
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (crawler_id, external_id)
);

CREATE TABLE crawl_errors (
    run_id          text NOT NULL REFERENCES crawl_runs ON DELETE CASCADE,
    external_id     text,
    url             text,
    reason          text NOT NULL,
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON crawl_errors (run_id);

-- "A crawler wrote this" is exactly the kind of thing the audit trail exists to
-- record, and it was not previously sayable: actor_mode admitted only 'user'
-- and 'platform', so crawled writes had to masquerade as one of them. The set
-- stays closed -- an open mode column stops being aggregatable, and "who acted"
-- is the question audit is least allowed to be vague about.
ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_actor_mode_check;
ALTER TABLE audit_events ADD CONSTRAINT audit_events_actor_mode_check
    CHECK (actor_mode IN ('user', 'platform', 'crawler'));
