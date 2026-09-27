-- A purged tombstone is not a ContentRef.
--
-- `one_content_ref` says an item is Inline, Stored or Pending -- exactly one.
-- That is correct for every live row and wrong for exactly one case: after the
-- cascade completes, the item legitimately has *no* content of any kind. It is
-- a metadata-only marker that the audit record points at.
--
-- Relaxing the invariant unconditionally would have been the easy fix and the
-- wrong one, because it is load-bearing everywhere else. Instead it is
-- conditioned on the one state where it stops applying.

ALTER TABLE data_items DROP CONSTRAINT one_content_ref;

ALTER TABLE data_items ADD CONSTRAINT one_content_ref CHECK (
    purged_at IS NOT NULL
    OR (content_text IS NOT NULL)::int
     + (storage_ref  IS NOT NULL)::int
     + (pending_ref  IS NOT NULL)::int = 1
);
