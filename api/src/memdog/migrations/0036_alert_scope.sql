-- What an alert is allowed to look at, as opposed to what it matches.
--
-- `where_clause` asks a question about the event's own fields. A scope asks a
-- different one -- *is this subject even mine to care about* -- and it cannot be
-- expressed in the payload, because the payload does not know which memory an
-- item is in, which case it belongs to, or who wrote it. Those are joins.
--
-- Collapsing the two would mean either denormalising membership onto every
-- transition, which goes stale the moment someone moves an item, or making the
-- selector able to run subqueries, which is a query language nobody asked for.

ALTER TABLE alerts ADD COLUMN scope jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN alerts.scope IS
  'Bounds which subjects count: memory_id, case_id, producer_id, entity_id. '
  'Resolved by join at evaluation time, never copied onto the event.';
