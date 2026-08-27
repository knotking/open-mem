-- Artifacts need the same tombstone every other visible row has.
--
-- The ACL predicate is shared by every read path, and it filters on
-- `deleted_at`. That is not incidental: deleting a source must invalidate what
-- was derived from it (FR-SCH-15), and an artifact with no way to be
-- invisible-from-this-instant cannot participate in that cascade.

-- `private` means owner-only, so a derived row needs an owner or it is visible
-- to nobody -- including the person whose document it summarises. The owner is
-- the owner of the most restrictive source, which is the same source the ACL
-- itself came from.
ALTER TABLE artifacts ADD COLUMN owner_id text REFERENCES users ON DELETE RESTRICT;

ALTER TABLE artifacts ADD COLUMN deleted_at timestamptz;
ALTER TABLE artifacts ADD COLUMN purged_at timestamptz;

-- The reverse lookup the erasure cascade walks: which artifacts absorbed this
-- item? Already indexed on artifact_sources(data_id); this covers the sweep in
-- the other direction, over what is still live.
CREATE INDEX artifacts_live ON artifacts (project_id, created_at DESC)
    WHERE deleted_at IS NULL;
