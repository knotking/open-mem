-- Keywords were produced and never usable.
--
-- Every enriched record carries `artifacts.keywords`, a text[] the model fills
-- with what the record is about. It was read in exactly one place: the row
-- returned beside a record you had already found. Nothing could search by one,
-- filter by one, or list them -- so the model did the work, the answer was
-- stored, and no part of the product could reach it.
--
-- The `tags` filter is not the same thing and must not be conflated with it. A
-- tag is an assertion by a person; a keyword is a model's guess. Merging them
-- would make the guess unfalsifiable -- you could no longer tell which of the
-- two said a record was about billing, and a wrong keyword would be
-- indistinguishable from a deliberate label.
--
-- GIN because every query against this column is containment (`&&` / `@>`), and
-- a btree cannot answer those. Without it, filtering by keyword is a sequential
-- scan of every artifact in the org, which is fine at a thousand records and
-- not at a million.
CREATE INDEX IF NOT EXISTS artifacts_keywords ON artifacts USING gin (keywords);

-- Aggregating "what is this project about" unnests every artifact's keywords
-- for one project, so the scan is bounded by project rather than by table.
CREATE INDEX IF NOT EXISTS artifacts_project_kind ON artifacts (project_id, kind);
