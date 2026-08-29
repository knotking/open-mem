-- Which run produced a record, so spend can be attributed to it.
--
-- `usage_events.run_id` has existed since the meter shipped, and nothing has
-- ever populated it. Not for want of the value: a crawl run knows its own id at
-- the moment it writes. The record simply had nowhere to carry it, so by the
-- time enrichment spent money on the item the connection was gone.
--
-- That is what keeps two requirements open. A dry run quotes an estimate --
-- "this will create 47,213 items and consume 84% of your monthly budget" -- and
-- without actuals to compare it against, the estimate drifts until nobody
-- trusts it, at which point the gate it guards becomes a formality people click
-- through.
--
-- **Set by the code that knows, never by the caller.** `write_items` takes it
-- as an argument and the HTTP endpoint does not pass one, so an external client
-- cannot claim to be part of a run it has nothing to do with. A field on the
-- write request would be an attribution anybody could assert.
--
-- **No foreign key.** Runs live in two tables -- `runs` for deletions and
-- reprocess, `crawl_runs` for crawls -- and a record should outlive the run
-- that produced it in any case. Provenance that vanishes when the job is tidied
-- up is not provenance.

ALTER TABLE data_items ADD COLUMN run_id text;

COMMENT ON COLUMN data_items.run_id IS
    'The crawl, import or reprocess run that produced this record, when one '
    'did. Attribution for what enrichment later spends on it.';

CREATE INDEX ON data_items (run_id) WHERE run_id IS NOT NULL;
