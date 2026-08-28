-- An assignment need not name an engine.
--
-- The original column was NOT NULL, which assumed every assignment picks a
-- registered provider. It does not: the common case is naming a model the
-- deployment already reaches through its configured default -- switching audio
-- to a purpose-built transcriber on the same provider, for instance. Requiring
-- an engine there would mean registering a duplicate of one that already works,
-- with its own copy of the credential.

ALTER TABLE model_assignments ALTER COLUMN engine_id DROP NOT NULL;
