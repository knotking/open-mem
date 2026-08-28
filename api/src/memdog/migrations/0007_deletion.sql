-- Deletion, as a run.
--
-- The shared run entity: bulk write, selector delete, account delete and
-- reprocess are the same shape -- a checkpointed, resumable job with per-item
-- results -- so they get one table rather than four.

CREATE TABLE runs (
    run_id      text PRIMARY KEY,
    org_id      text NOT NULL,
    project_id  text,
    kind        text NOT NULL CHECK (kind IN ('delete', 'reprocess', 'bulk_write', 'account_delete')),
    status      text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    mode        text NOT NULL DEFAULT 'execute' CHECK (mode IN ('dry_run', 'execute')),
    selector    jsonb NOT NULL DEFAULT '{}',
    reason      text,
    -- Resumable means restarting at the step it failed on, not at step 1.
    checkpoint  text,
    total       int NOT NULL DEFAULT 0,
    done        int NOT NULL DEFAULT 0,
    failed      int NOT NULL DEFAULT 0,
    retained    int NOT NULL DEFAULT 0,
    actor_user_id text,
    actor_key_id  text,
    detail      jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE INDEX runs_org ON runs (org_id, created_at DESC);

CREATE TABLE run_items (
    run_id      text NOT NULL REFERENCES runs ON DELETE CASCADE,
    data_id     text NOT NULL,
    status      text NOT NULL CHECK (status IN ('pending', 'done', 'failed', 'retained')),
    reason      text,
    at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, data_id)
);

-- The tombstone is metadata-only: the row survives so the cascade knows which
-- chunks, blobs and contributions belonged to it. Remove the root first and a
-- cascade that fails halfway has lost its own map.
-- Legal hold outranks erasure. Without it, an erasure request either fails
-- wholesale or quietly deletes something a court said to keep.
ALTER TABLE data_items ADD COLUMN legal_hold boolean NOT NULL DEFAULT false;
ALTER TABLE data_items ADD COLUMN deletion_reason text;
ALTER TABLE data_items ADD COLUMN deletion_run_id text;

-- Un-purged tombstones are the metric that catches a cascade that quietly
-- stopped at the blob step (FR-DEL-19).
CREATE INDEX data_items_unpurged ON data_items (deleted_at)
    WHERE deleted_at IS NOT NULL AND purged_at IS NULL;

-- A summary spanning forty items, one of which is erased, still contains the
-- erased content in prose. Deleting it loses value; leaving it is a compliance
-- failure. Marking it stale hands it to reprocess (FR-DEL-7).
ALTER TABLE artifacts ADD COLUMN stale_reason text;
