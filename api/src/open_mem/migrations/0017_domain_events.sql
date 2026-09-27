-- The domain event log.
--
-- Until now the queue was the record of what still needed doing, and the
-- reconciler existed to repair that assumption by re-deriving work from the
-- rows. This inverts it: **the log is the record**, and the queue is only how
-- events reach a worker. A lost message is then a dispatch problem, not a lost
-- intention.
--
-- Three properties it has to carry:
--
--   * **Order.** An enrichment cannot be processed before the data it enriches
--     was recorded. `caused_by` makes that a fact about the data rather than a
--     hope about timing.
--   * **Durability with the write.** The event is inserted in the same
--     transaction as the row it describes, so "recorded but no event" cannot
--     happen.
--   * **Events with no consumer are still events.** Graph events are emitted
--     today and consumed later; the history has to exist before the consumer
--     does, or the graph starts empty at whatever date someone gets round to it.

CREATE TABLE domain_events (
    event_id     text PRIMARY KEY,
    -- Monotonic per deployment. Ordering by ULID would be *nearly* right; a
    -- sequence is exactly right, and dispatch order is not a place for nearly.
    sequence     bigserial NOT NULL,
    event_type   text NOT NULL,
    org_id       text NOT NULL,
    project_id   text,
    data_id      text,
    -- The event this one depends on. Dispatch refuses to run ahead of it.
    caused_by    text REFERENCES domain_events(event_id),
    actor_user_id text,
    actor_key_id text,
    payload      jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'dispatched', 'consumed', 'failed', 'no_consumer')),
    attempts     int NOT NULL DEFAULT 0,
    last_error   text,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    dispatched_at timestamptz,
    consumed_at  timestamptz
);

CREATE INDEX domain_events_dispatch ON domain_events (status, sequence)
    WHERE status IN ('pending', 'dispatched');
CREATE INDEX domain_events_data ON domain_events (data_id, sequence);
CREATE INDEX domain_events_type ON domain_events (org_id, event_type, occurred_at DESC);
