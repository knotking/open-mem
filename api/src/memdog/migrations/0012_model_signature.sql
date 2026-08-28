-- The signature of the model that actually answered.
--
-- `generator_version` fingerprints the *configuration*: prompt, model id,
-- schema, parser, chunker. It does not fingerprint the model. `gemini-3.7-flash`
-- is an alias whose weights change underneath you, so two artifacts with an
-- identical generator_version can come from different builds -- and nothing in
-- the schema would show it.
--
-- Providers report the build they used and an id for the call. Recording both
-- is what makes "reproduce this artifact" and "which output came from the
-- model that regressed last Tuesday" answerable questions.

ALTER TABLE artifacts ADD COLUMN model_version text;
ALTER TABLE artifacts ADD COLUMN response_id text;

ALTER TABLE embeddings ADD COLUMN model_version text;
-- Embeddings are written in bulk, so an id per row would be noise; the
-- generator and the reported build are what identify the vector space.
ALTER TABLE data_versions ADD COLUMN model_version text;
ALTER TABLE data_versions ADD COLUMN response_id text;

CREATE INDEX artifacts_model_version ON artifacts (model_id, model_version);
