-- Alerts: declare what is worth knowing about, and record it when it happens.
--
-- **Transitions are `domain_events` rows.** No new table for them: that log
-- already has a monotonic `sequence`, a jsonb payload, actor columns, and is
-- already emitted inside the caller's transaction. A second log would duplicate
-- every one of those and add a second place work can be lost.
--
-- An alert's `watermark` is therefore a `domain_events.sequence`, and evaluation
-- reads forward from it **independently of the dispatch status machinery**. That
-- is deliberate: a message dropped between publish and handler costs latency,
-- never an alert, because the rows were the record all along.

CREATE TABLE alerts (
    alert_id        text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    name            text NOT NULL,
    -- One row and one evaluation path for both. 'rule' stops after the
    -- selector; 'llm' runs a description over whatever the selector left.
    mode            text NOT NULL DEFAULT 'rule' CHECK (mode IN ('rule', 'llm')),
    -- Which kind of transition this watches. Closed, like every other
    -- vocabulary here: an open one degrades into unqueryable free text.
    surface         text NOT NULL,
    -- Required in BOTH modes. An llm alert with no selector would run a model
    -- against every transition in the project, and the cost is unbounded in
    -- exactly the way nobody notices until the bill.
    where_clause    jsonb NOT NULL DEFAULT '{}',
    describe        text,
    model_id        text,
    -- How long the consumer coalesces before evaluating. The knob that decides
    -- whether this is async batching or per-write evaluation wearing a queue.
    debounce_seconds int NOT NULL DEFAULT 5,
    -- The sequence this alert has evaluated up to. Advanced ONLY by a completed
    -- evaluation: a partial one that advanced it steps over transitions nobody
    -- looked at, and nothing ever comes back for them.
    watermark       bigint NOT NULL DEFAULT 0,
    batch_cap       int NOT NULL DEFAULT 500,
    overlap         text NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip', 'queue')),
    -- Bumped on any edit that changes what matches, which invalidates the
    -- backtest -- the same reason editing a crawler invalidates its dry run
    -- rather than carrying an approval forward onto a question that changed.
    config_version  int NOT NULL DEFAULT 1,
    backtested_version int,
    enabled         boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    CHECK (mode <> 'llm' OR describe IS NOT NULL),
    UNIQUE (project_id, name)
);

CREATE INDEX alerts_due ON alerts (project_id, watermark) WHERE enabled AND deleted_at IS NULL;

-- What each evaluation did. `deferred` is why this table exists: a run that
-- silently truncated at batch_cap reads as "nothing else matched", which for an
-- alert system is the worst available lie.
CREATE TABLE alert_runs (
    run_id          text PRIMARY KEY,
    alert_id        text NOT NULL REFERENCES alerts ON DELETE CASCADE,
    config_version  int NOT NULL,
    trigger         text NOT NULL CHECK (trigger IN ('consumer', 'tick', 'backtest')),
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'skipped')),
    from_sequence   bigint NOT NULL,
    to_sequence     bigint,
    candidates      int NOT NULL DEFAULT 0,
    matches         int NOT NULL DEFAULT 0,
    deferred        int NOT NULL DEFAULT 0,
    -- No `model_calls` yet, and no `evidence_span` on observed_events. Both
    -- belong to `llm` mode, which is refused at creation until it is built --
    -- and a column nothing writes is a claim that something works.
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX alert_runs_alert ON alert_runs (alert_id, started_at DESC);
-- The overlap check: is this alert already evaluating?
CREATE INDEX alert_runs_live ON alert_runs (alert_id) WHERE status = 'running';

-- The record of every match. Recording is the commitment; delivery is dispatch.
CREATE TABLE observed_events (
    event_id        text PRIMARY KEY,
    -- The poll cursor. A timestamp would order two events in the same
    -- millisecond by luck.
    sequence        bigserial NOT NULL,
    alert_id        text NOT NULL REFERENCES alerts ON DELETE CASCADE,
    config_version  int NOT NULL,
    run_id          text REFERENCES alert_runs ON DELETE SET NULL,
    org_id          text NOT NULL,
    project_id      text NOT NULL,
    surface         text NOT NULL,
    -- The transition this came from, so a reader can go back to the cause.
    source_event_id text REFERENCES domain_events(event_id) ON DELETE SET NULL,
    -- Whichever the surface implies; all nullable because no surface fills all.
    data_id         text,
    fact_id         text,
    memory_id       text,
    case_id         text,
    entity_id       text,
    payload         jsonb NOT NULL DEFAULT '{}',
    matched_by      text NOT NULL CHECK (matched_by IN ('selector', 'model')),
    confidence      real,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    -- One alert fires once per transition. A re-run after a crash must not
    -- double-report, and this is cheaper than making every consumer idempotent.
    UNIQUE (alert_id, source_event_id)
);

-- NOTE the absence: there is no access_level here, and that is the point.
-- Visibility is the SUBJECT's, resolved at read and at delivery against the
-- reader's rights at that moment. A copy taken at match time goes stale the
-- moment the subject is re-shared, and ignores a revocation that happened
-- between matching and delivering -- and notification is a side channel around
-- the whole access model, so it is the one place a stale copy cannot be
-- tolerated.

CREATE INDEX observed_events_cursor ON observed_events (sequence);
CREATE INDEX observed_events_alert ON observed_events (alert_id, occurred_at DESC);
CREATE INDEX observed_events_project ON observed_events (project_id, sequence);
