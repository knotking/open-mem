-- Checkpoint timelines: a memory that answers "what changed since last time".
--
-- A memory is otherwise a bag of records with a lifetime. Nothing in it records
-- that *this* record is a later version of the same thing as *that* one, so a
-- corpus fed the same document repeatedly -- a nightly export, a weekly status
-- report, a vendor feed, a policy that keeps being reissued -- accumulates
-- copies and answers from all of them at once. The question people actually
-- have about such a memory is not "what does it say" but "what moved", and
-- there was no way to ask it.
--
-- **A flag on the type, not a new type.** `0006_memories.sql` argues against
-- baking semantics into the type vocabulary -- "not a taxonomy: earlier drafts
-- shipped ten types with semantics baked into each". This is the same shape as
-- `on_expiry`: a policy a project sets on a type it already uses, so a
-- `vendor_feed` becomes a timeline without being re-typed into one.
ALTER TABLE memory_types
    ADD COLUMN IF NOT EXISTS checkpoints boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS memory_checkpoints (
    checkpoint_id   text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    memory_id       text NOT NULL REFERENCES memories ON DELETE CASCADE,
    -- The record this checkpoint is of. Deleting it takes the checkpoint: a
    -- checkpoint whose record is gone is a claim about nothing.
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    -- Position in the timeline, 1-based. Dense and per-memory, because the
    -- whole structure is "the one before this one" and a sparse ordering by
    -- timestamp cannot express that when two records arrive in one write.
    seq             int NOT NULL,

    -- Copied at capture rather than joined at compare time. The record's
    -- checksum can move underneath a checkpoint -- a re-parse writes a new
    -- revision -- and the comparison must be against what was actually seen,
    -- not against what the row says today.
    checksum        text,

    -- The chain. NULL means first in the timeline, which is a real state and
    -- not a missing one: there is nothing to compare against, and saying so is
    -- different from having compared and found nothing.
    previous_id     text REFERENCES memory_checkpoints ON DELETE SET NULL,

    -- What this record says, on its own, and what changed against the one
    -- before it. Both are ordinary artifacts, so both carry an ACL, their
    -- sources, and the generator fingerprint that produced them.
    state_artifact_id  text REFERENCES artifacts ON DELETE SET NULL,
    change_artifact_id text REFERENCES artifacts ON DELETE SET NULL,

    -- Two columns, because "did the check run" and "what did it find" are
    -- different questions and one column answering both is how a failed run
    -- comes to read as "nothing changed" -- the worst available lie for a
    -- change detector. Same split as `repo_snapshots`.
    status          text NOT NULL DEFAULT 'pending',
    outcome         text,
    -- Why it stopped, or why it could not compare. A failed checkpoint with
    -- nothing here is indistinguishable from one nobody has got to yet.
    reason          text,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT memory_checkpoints_status CHECK (
        status IN ('pending', 'running', 'complete', 'failed')
    ),
    -- `unchanged` is a first-class answer, never an absence -- the same rule
    -- the findings schema follows, where `[]` and a missing key are different
    -- claims. `incomparable` is the generator-drift guard: two summaries made
    -- by different prompts differ because the prompt moved, and a diff of them
    -- reports that as content change.
    CONSTRAINT memory_checkpoints_outcome CHECK (
        outcome IS NULL
        OR outcome IN ('first', 'changed', 'unchanged', 'incomparable')
    ),
    -- One checkpoint per record per memory. Re-adding a record already in a
    -- memory is not a new checkpoint, and this is the constraint that says so
    -- rather than a check somewhere that can be forgotten.
    UNIQUE (memory_id, data_id),
    UNIQUE (memory_id, seq)
);

-- The two reads this table serves: one timeline newest first, and the worker
-- picking up what has not been checked.
CREATE INDEX IF NOT EXISTS memory_checkpoints_timeline
    ON memory_checkpoints (memory_id, seq DESC);
CREATE INDEX IF NOT EXISTS memory_checkpoints_pending
    ON memory_checkpoints (created_at) WHERE status IN ('pending', 'running');
