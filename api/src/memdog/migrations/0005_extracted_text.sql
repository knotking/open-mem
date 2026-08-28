-- Extracted text is not a fourth ContentRef case.
--
-- The `one_content_ref` invariant says an item is Inline, Stored or Pending --
-- exactly one. Parsing a PDF or transcribing an audio file appeared to break
-- it: the item now has both the original bytes and text derived from them.
--
-- Relaxing the constraint would have been the easy fix and the wrong one. The
-- invariant is correct: `content_text` means "the caller sent text", and a
-- transcript is not something the caller sent. It is a *derivation of the raw
-- bytes*, which is exactly the distinction the blob layout already draws with
-- its raw / text / derived kinds.
--
-- So extraction gets its own column, the raw ref stays untouched, and the
-- original always survives its own extraction -- which is what makes
-- re-parsing with a better handler possible instead of a re-ingest.

ALTER TABLE data_items ADD COLUMN extracted_text text;

-- What the indexer reads: what the caller sent, or failing that, what we made
-- of what they sent. One expression, used everywhere, so the two can never
-- drift apart in different queries.
ALTER TABLE data_items ADD COLUMN indexable_text text
    GENERATED ALWAYS AS (coalesce(content_text, extracted_text)) STORED;

CREATE INDEX data_items_needs_indexing ON data_items (project_id)
    WHERE deleted_at IS NULL AND state = 'stored'
      AND coalesce(content_text, extracted_text) IS NOT NULL;
