-- Compaction: fold a memory's members into a derived artifact so the working
-- set stops growing, without losing anything.
--
-- The central choice is that **compaction never deletes**. mem0 reconciles by
-- overwriting and what it replaces is gone; the temporal graph here shipped on
-- the opposite premise -- a claim is closed, never replaced, so `as_of` can
-- still answer what was believed in March. A compaction that destroyed its
-- inputs would make `as_of` lie about everything it touched.
--
-- memories.md already stated the rule: archived originals stay searchable, so
-- compression is a **retrieval default rather than a deletion**.

-- Archival is a column, not a state. `state` is the readiness staircase --
-- stored, searchable, enriched -- and an archived item is still all three. It is
-- findable when asked for; it is merely out of the working set. Folding the two
-- together would make "is this searchable" and "is this current" one question.
ALTER TABLE data_items ADD COLUMN archived_at timestamptz;
ALTER TABLE data_items ADD COLUMN archived_by text;   -- the run that did it

CREATE INDEX data_items_live ON data_items (project_id, created_at DESC)
    WHERE archived_at IS NULL AND deleted_at IS NULL;

CREATE TABLE compaction_jobs (
    job_id          text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    name            text NOT NULL,
    -- What to compact. A memory rather than a filter, because that is the
    -- container people already think in and the axis the corpus is organised on.
    memory_id       text NOT NULL REFERENCES memories ON DELETE CASCADE,
    -- How. Served from a registry so a console never holds a second copy that
    -- can drift from what the server will actually run.
    algorithm       text NOT NULL,
    options         jsonb NOT NULL DEFAULT '{}',
    -- The crawler's shape, and for the crawler's reasons.
    schedule        jsonb NOT NULL DEFAULT '{"type":"manual"}',
    next_due_at     timestamptz,
    -- A run that lands while the previous is live is recorded as skipped, not
    -- queued: queueing guarantees a backlog for any job slower than its interval.
    overlap         text NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip', 'queue')),
    -- Bumped on any edit to what it would do, which invalidates a dry run --
    -- the same reason editing a crawler invalidates its.
    config_version  int NOT NULL DEFAULT 1,
    dry_run_version int,
    enabled         boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    UNIQUE (project_id, name)
);

CREATE INDEX compaction_jobs_due ON compaction_jobs (next_due_at)
    WHERE enabled AND deleted_at IS NULL;

-- What each run did, in the terms somebody deciding whether to keep the job
-- would ask: how much did it look at, how much did it fold away, what did that
-- cost, and how much smaller is the working set now.
CREATE TABLE compaction_runs (
    run_id          text PRIMARY KEY,
    job_id          text REFERENCES compaction_jobs ON DELETE CASCADE,
    memory_id       text NOT NULL,
    org_id          text NOT NULL,
    algorithm       text NOT NULL,
    config_version  int,
    -- A dry run reports what a live one would do and writes nothing. Same code
    -- path, so what it reports is what would actually happen.
    mode            text NOT NULL DEFAULT 'live' CHECK (mode IN ('dry', 'live')),
    trigger         text NOT NULL CHECK (trigger IN ('manual', 'schedule')),
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed','skipped')),
    considered      int NOT NULL DEFAULT 0,
    archived        int NOT NULL DEFAULT 0,
    artifacts       int NOT NULL DEFAULT 0,
    bytes_before    bigint NOT NULL DEFAULT 0,
    bytes_after     bigint NOT NULL DEFAULT 0,
    model_calls     int NOT NULL DEFAULT 0,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX compaction_runs_job ON compaction_runs (job_id, started_at DESC);
CREATE INDEX compaction_runs_live ON compaction_runs (job_id) WHERE status = 'running';
