-- A postponed check is not a failed one.
--
-- `run_check` writes `failed` before re-raising, and `workers.handle_checkpoint`
-- then classifies a capacity error -- a rate limit, an exhausted budget -- as
-- *deferred* and lets the queue try again. So the row said `failed` about work
-- that was merely waiting its turn, and would go on to succeed.
--
-- That was tolerable while only the console read it. `0053`'s
-- `checkpoint.checked` surface put it on the event stream, where it announces
-- "the check did not finish" for something that finishes fine ten seconds
-- later -- and an alert that cries wolf is one people switch off, taking the
-- real failures with it.
--
-- `deferred` is its own terminal-for-now state: the check stopped, it will be
-- picked up again, and nothing is wrong. It is deliberately *not* folded back
-- into `pending`, because "nobody has looked at this yet" and "a model refused
-- us and we are waiting" are different facts, and the second one is the whole
-- reason somebody would go looking at provider quota.
ALTER TABLE memory_checkpoints
    DROP CONSTRAINT IF EXISTS memory_checkpoints_status;

ALTER TABLE memory_checkpoints
    ADD CONSTRAINT memory_checkpoints_status CHECK (
        status IN ('pending', 'running', 'complete', 'failed', 'deferred')
    );

-- The sweep has to see them, or a deferred check waits for a queue message that
-- was already consumed and never comes back. `0053`'s partial index covers
-- `pending` and `running` only, so it is replaced rather than added to: an
-- index that does not cover the query it exists for is worse than none,
-- because it looks like the query is served.
DROP INDEX IF EXISTS memory_checkpoints_pending;
CREATE INDEX IF NOT EXISTS memory_checkpoints_pending
    ON memory_checkpoints (created_at)
    WHERE status IN ('pending', 'running', 'deferred');
