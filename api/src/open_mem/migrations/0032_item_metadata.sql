-- `metadata` on a write item was accepted and then discarded.
--
-- The contract has carried `WriteItem.metadata` since the spine shipped, the
-- write-api doc's own example puts `{"tags": ["source:salesforce"]}` in it, and
-- nothing has ever read it. There was no column to read it into. So every
-- producer following the documented shape -- and the crawler, which builds a
-- `crawler:<id>` tag, the item's title and its source URL into exactly that
-- field -- had them dropped between the request body and the insert.
--
-- It went unnoticed because nothing errors. The write succeeds, the item is
-- durable and searchable, and only the provenance is missing; you find out when
-- you try to answer "which of these did that crawler pull?" and cannot.
--
-- **Two columns, because the two things are different.** `tags` already exists
-- and is a `text[]` people filter on; `metadata` is whatever the producer
-- wanted to keep alongside the record. Lifting `metadata.tags` into the `tags`
-- column at write time is what makes the documented shape mean what it looks
-- like it means, without breaking anyone already sending the top-level field.
--
-- **Not indexed.** No query filters on it yet, and a GIN index on a jsonb
-- column nobody queries is maintenance cost for a lookup nobody performs. The
-- selectors that needed to exist -- `run_id`, `tags` -- have real columns.

ALTER TABLE data_items ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN data_items.metadata IS
    'Producer-supplied context kept alongside the record: a title, a source '
    'URL, whatever the writer wanted to carry. Never consulted for access '
    'control -- the ACL is sealed before any caller-supplied value is read.';
