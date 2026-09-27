-- The net answer for a span of a checkpoint timeline, kept so it is paid for
-- once.
--
-- `0053_memory_checkpoints.sql` gave a timeline one comparison per step, and
-- `changes_between` composes those steps into a range for free. Composing
-- reports *churn*: a value that went green, red, green appears as two changes,
-- because two changes happened. The other reading -- what is different between
-- these two points, net of everything in between -- is a different question,
-- and the only way to answer it is to compare the two descriptions directly.
--
-- That costs a model call, and a polling consumer asks for the same range
-- repeatedly, so the answer is stored rather than recomputed.
CREATE TABLE IF NOT EXISTS memory_change_ranges (
    range_id            text PRIMARY KEY,
    org_id              text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id          text NOT NULL REFERENCES projects ON DELETE CASCADE,
    memory_id           text NOT NULL REFERENCES memories ON DELETE CASCADE,

    -- The two ends. Deleting either takes the range: a net answer whose
    -- endpoints are gone is a claim about nothing, and the same argument
    -- `memory_checkpoints.data_id` makes about its record.
    from_checkpoint_id  text NOT NULL REFERENCES memory_checkpoints ON DELETE CASCADE,
    to_checkpoint_id    text NOT NULL REFERENCES memory_checkpoints ON DELETE CASCADE,

    -- **Part of the key, not a column beside it.** Two ranges computed by
    -- different generator versions are different answers, and serving the older
    -- one after a prompt moved is exactly the drift `run_check` already refuses
    -- to compare through. Putting the version in the unique key means a changed
    -- prompt misses the cache rather than silently answering from it.
    generator_version   text NOT NULL,

    -- The answer itself is an ordinary artifact, so it carries an ACL, its
    -- sources, and the generator fingerprint -- and the erasure cascade reaches
    -- it through the same path as every other derived thing. NULL when the pair
    -- could not be compared, which is a recorded outcome rather than a gap.
    change_artifact_id  text REFERENCES artifacts ON DELETE SET NULL,

    -- Two columns, for the reason `memory_checkpoints` has two: "did the
    -- comparison run" and "what did it find" are different questions, and one
    -- column answering both is how a failed run comes to read as "nothing
    -- changed".
    status              text NOT NULL DEFAULT 'complete',
    outcome             text,
    reason              text,

    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT memory_change_ranges_status CHECK (
        status IN ('complete', 'failed')
    ),
    -- The same vocabulary the per-step outcome uses, minus `first`: a range has
    -- two ends by construction, so there is no such thing as a range with
    -- nothing before it.
    CONSTRAINT memory_change_ranges_outcome CHECK (
        outcome IS NULL
        OR outcome IN ('changed', 'unchanged', 'incomparable')
    ),
    UNIQUE (memory_id, from_checkpoint_id, to_checkpoint_id, generator_version)
);

-- The one read this table serves: is this exact range, at this exact generator
-- version, already answered. Covered by the unique key above, so no second
-- index -- an unused index on a table written once per paid comparison is
-- storage and write cost for nothing.
