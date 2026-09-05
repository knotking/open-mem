-- Repository snapshots -- a repo at one commit, analysed once.
--
-- The table exists for one column combination: `(project_id, repo_url,
-- commit_sha)` is UNIQUE, and that constraint *is* the "per snapshot only"
-- rule. Analysis costs a clone, a full AST parse and four model calls; two
-- people pasting the same URL within a second of each other is the ordinary
-- case, and a check-then-insert would let both through and bill twice. Putting
-- it in the schema is the only version that cannot be forgotten by a caller.
--
-- What is deliberately *not* here:
--
-- **No report column.** A report is an artifact, keyed by `generator_version`,
-- which already gives re-use on an unchanged prompt and staleness on a changed
-- one. A reports column would be a second, worse copy of that mechanism with
-- none of the invalidation.
--
-- **No parent snapshot, no diff.** Snapshots of one repository are correlated
-- by their shared case and by nothing else. A `previous_snapshot_id` would be
-- the beginning of a comparison feature, and half of one is worse than none:
-- reports that look comparable across two commits and are not.
--
-- **No branch as identity.** `ref` is recorded because a person asked for
-- `main` and wants to see that they did, but the sha is what the row is keyed
-- by. A report attributed to a moving branch is one nobody can reproduce.
CREATE TABLE IF NOT EXISTS repo_snapshots (
    snapshot_id   text PRIMARY KEY,
    org_id        text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id    text NOT NULL REFERENCES projects ON DELETE CASCADE,
    -- The repository, across every snapshot of it. A case answers "what is this
    -- about"; the memory below answers "how long does this one matter".
    case_id       text NOT NULL REFERENCES cases ON DELETE CASCADE,
    -- This snapshot's records live here: graph.json, the graph report, the
    -- manifests, and the bounded file set the analysers read. Deleting the
    -- memory takes the snapshot with it, which is the behaviour wanted -- the
    -- row without its records is a claim with nothing behind it.
    memory_id     text NOT NULL REFERENCES memories ON DELETE CASCADE,
    repo_url      text NOT NULL,
    commit_sha    text NOT NULL,
    ref           text,
    status        text NOT NULL DEFAULT 'pending',
    -- Why it stopped, or what it skipped. A failed snapshot with nothing here
    -- is indistinguishable from one nobody ran, and that difference is the
    -- first thing anyone looking at the screen wants.
    reason        text,
    -- Counts the job discovered: files, symbols, edges, languages, bytes, and
    -- what the resolve step learned from GitHub. Denormalised on purpose --
    -- these are read on every list row and computing them from the records
    -- would be a join per row for numbers that never change after the run.
    stats         jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT repo_snapshots_status CHECK (
        status IN ('pending', 'running', 'complete', 'failed')
    ),
    -- Lowercase, 40 hex. A short sha would let one commit be analysed twice
    -- under two spellings, which is exactly what the unique key exists to stop.
    CONSTRAINT repo_snapshots_sha CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT repo_snapshots_once UNIQUE (project_id, repo_url, commit_sha)
);

-- The two reads this table serves: one repository's snapshots newest first, and
-- the job picking up what it has been given.
CREATE INDEX IF NOT EXISTS repo_snapshots_case
    ON repo_snapshots (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS repo_snapshots_pending
    ON repo_snapshots (created_at) WHERE status IN ('pending', 'running');
