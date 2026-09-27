-- Typed relationships between entities. Graph layer 2.
--
-- Two kinds of connection exist in this system and only one of them is here.
--
-- An *asserted* edge is a claim a document made -- "Priya works for Northwind"
-- -- and it needs a predicate, a source and a confidence, because somebody
-- said it and they can be wrong.
--
-- A *co-mention* is not stored at all. Two entities named in the same record
-- are connected by that fact alone, and it is already recorded in
-- entity_mentions; materialising it would duplicate a join and let the copy go
-- stale. It is computed at query time instead.
--
-- The distinction matters beyond storage: a co-mention is evidence of nothing
-- in particular, while an asserted edge is a specific claim. Merging them into
-- one table would lose which is which.

CREATE TABLE entity_edges (
    edge_id         text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    subject_id      text NOT NULL REFERENCES entities ON DELETE CASCADE,
    -- Closed vocabulary. An open one degrades into unqueryable free text, and
    -- the entire value of a typed edge is being able to ask for all of them.
    predicate       text NOT NULL CHECK (predicate IN (
                        'works_for', 'member_of', 'reports_to', 'collaborates_with',
                        'located_in', 'part_of', 'owns', 'produces', 'uses',
                        'attended', 'about', 'related_to')),
    object_id       text NOT NULL REFERENCES entities ON DELETE CASCADE,
    -- The record that said so. An edge without evidence is an assertion nobody
    -- can check, which is the failure mode that makes extracted graphs
    -- untrustworthy.
    source_data_id  text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    confidence      real NOT NULL DEFAULT 0.5,
    generator_version text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- The same claim from two documents is two pieces of evidence for one
    -- edge, not two edges -- but the same claim twice from one document is one.
    UNIQUE (subject_id, predicate, object_id, source_data_id)
);

CREATE INDEX ON entity_edges (subject_id);
CREATE INDEX ON entity_edges (object_id);
CREATE INDEX ON entity_edges (project_id, predicate);
CREATE INDEX ON entity_edges (source_data_id);
