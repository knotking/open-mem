-- A rollup that is out of date, and says so.
--
-- `derived_from` is the relation where a child changing does not change the
-- parent -- it makes the parent **wrong**. `part_of` needs none of this: the
-- parent's members *are* its children's, so there is no separate state to keep
-- in sync and nothing that can go stale.
--
-- A flag rather than a queue entry, deliberately. One bulk import touching
-- forty children would enqueue forty recomputes of the same rollup; marking is
-- idempotent, so it costs one row write however many children moved, and a
-- single recompute clears it.
--
-- `stale_since` keeps the *first* change rather than the most recent one: the
-- question a reader asks is "how long has this been wrong", and overwriting it
-- on every subsequent child answers "when did it last get worse", which is not
-- the same and is less useful.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS stale_since timestamptz;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS stale_reason text;

-- Only stale rows, because that is the whole query: "what is out of date".
CREATE INDEX IF NOT EXISTS memories_stale
    ON memories (project_id) WHERE stale_since IS NOT NULL;
