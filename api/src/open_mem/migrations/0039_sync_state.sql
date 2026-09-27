-- Where a crawler got to, and what its credential is allowed to spend.
--
-- Three things were conflated in one `crawlers.watermark`, and separating them
-- is most of this migration.

-- 1. The cursor is per **scope**, not per crawler.
--
-- One Slack crawler over forty channels had a single watermark: a busy channel
-- drags it forward and thirty quiet ones are re-scanned from that point forever,
-- or the reverse and the busy one is skipped. `crawlers.watermark` stays as the
-- single-scope case, so nothing existing changes shape.
--
-- `cursor` is **opaque on purpose**. A Jira cursor is a timestamp, a GitHub one
-- is an etag, a Salesforce one is a `nextRecordsUrl`. The moment this column
-- tries to be a timestamp it stops fitting half the catalogue.
CREATE TABLE crawl_cursors (
    crawler_id  text NOT NULL REFERENCES crawlers ON DELETE CASCADE,
    -- Whatever the template's placeholder binds: "#eng", "owner/repo", "PROJ".
    -- Empty string is the crawler as a whole, which is today's behaviour.
    scope       text NOT NULL DEFAULT '',
    cursor      text,
    kind        text NOT NULL DEFAULT 'watermark'
                CHECK (kind IN ('watermark', 'etag', 'page')),
    items_seen  bigint NOT NULL DEFAULT 0,
    -- The number every downstream signal depends on. A project signal computed
    -- over a source that stopped syncing is confidently wrong, and "no activity
    -- for 7 days" is indistinguishable from "the connector broke 7 days ago"
    -- without it.
    last_ok_at  timestamptz,
    last_error  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (crawler_id, scope)
);

CREATE INDEX crawl_cursors_stale ON crawl_cursors (last_ok_at);

-- 2. What is left of a rate limit belongs to the **credential**, not the job.
--
-- Two crawlers sharing one Slack connection draw on the same quota and neither
-- can see the other, so this cannot live on `crawlers`. The limit itself is a
-- property of the API and lives in the connector template, beside the URL that
-- knows it.
ALTER TABLE connections ADD COLUMN limited_until timestamptz;
ALTER TABLE connections ADD COLUMN spent_today int NOT NULL DEFAULT 0;
ALTER TABLE connections ADD COLUMN spend_reset_at timestamptz;
ALTER TABLE connections ADD COLUMN last_limit_reason text;

-- 3. A run that could not start says why.
--
-- `skipped: rate limited` is a different fact from a run that found nothing,
-- and without the distinction a cooling token looks exactly like a quiet source.
ALTER TABLE crawl_runs ADD COLUMN scope text;

-- `crawl_runs.status` already allows 'skipped'? It does not -- the existing
-- CHECK lists pending, running, paused, completed, partial, failed, cancelled
-- and interrupted. A rate-limited attempt is none of those: it is not a failure
-- of the crawl, and calling it one would make a healthy source look broken.
ALTER TABLE crawl_runs DROP CONSTRAINT IF EXISTS crawl_runs_status_check;
ALTER TABLE crawl_runs ADD CONSTRAINT crawl_runs_status_check
    CHECK (status IN ('pending', 'running', 'paused', 'completed', 'partial',
                      'failed', 'cancelled', 'interrupted', 'rate_limited'));
