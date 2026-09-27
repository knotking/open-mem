-- Every revision, with what changed. Nothing is mutated in place.
--
-- The reason this is a table rather than an updated_at column: an item's text
-- is produced by a *pipeline*, and the pipeline changes. A PDF parsed by a
-- better handler, an audio file transcribed by a better model, a re-crawl that
-- brought new content -- each is a revision with a different generator behind
-- it, and "why does this say something different than last week" has no answer
-- without them.

CREATE TABLE data_versions (
    version_id      text PRIMARY KEY,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    revision        int NOT NULL,
    source          text NOT NULL,          -- write | parse | interpret | reprocess
    content_text    text,
    content_chars   int,
    checksum        text,
    mime_type       text,
    -- What produced this revision. A transcript from gemini-2.5-flash and one
    -- from a local Whisper are different revisions of the same item, and the
    -- difference is only legible if the producer is recorded.
    model_id        text,
    generator_version text,
    tokens          int,
    detail          jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_id, revision)
);

CREATE INDEX data_versions_lookup ON data_versions (data_id, revision DESC);
