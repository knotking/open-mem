-- Templates, and the five predicates they needed.
--
-- The graph had twelve predicates and seven of them are org-chart shaped --
-- works_for, reports_to, member_of, collaborates_with, owns, produces, uses.
-- That is the right vocabulary for the corpus this system was first pointed
-- at, and it has nothing to say about an argument. Run it over a philosophical
-- text and almost every edge becomes `related_to`, which asserts that two
-- words occurred near each other. The graph fills up and cannot be queried.
--
-- The five added here are not domain-specific, which is the test a predicate
-- has to pass before it earns a place in a shared vocabulary:
--
--   teaches        who puts an idea forward -- attribution, which the graph
--                  could not express at all. `about` attaches a topic to a
--                  document but cannot name who claimed it, and for an
--                  argument the claim and the claimant are one fact.
--   leads_to       directed consequence. A causal chain rendered as a set of
--                  `related_to` edges asserts the opposite of what an ordered
--                  chain says.
--   contrasts_with what is set against what -- the rejected alternative in a
--                  design document, the opposing quality in a doctrine.
--   caused_by      the postmortem's question, and deliberately distinct from
--                  leads_to: one is what a text argues follows, the other is
--                  what an investigation concluded happened.
--   mitigated_by   what limited or ended it.
--
-- A CHECK is altered by dropping and re-adding it, and both tables carry the
-- same list for the same reason -- entity_edges is the evidence and
-- entity_facts is the claim, and a predicate valid in one must be valid in the
-- other or enrichment fails halfway through its own transaction.
ALTER TABLE entity_edges DROP CONSTRAINT entity_edges_predicate_check;
ALTER TABLE entity_edges ADD CONSTRAINT entity_edges_predicate_check
    CHECK (predicate IN (
        'works_for', 'member_of', 'reports_to', 'collaborates_with',
        'located_in', 'part_of', 'owns', 'produces', 'uses',
        'attended', 'about', 'related_to',
        'teaches', 'leads_to', 'contrasts_with', 'caused_by', 'mitigated_by'));

ALTER TABLE entity_facts DROP CONSTRAINT entity_facts_predicate_check;
ALTER TABLE entity_facts ADD CONSTRAINT entity_facts_predicate_check
    CHECK (predicate IN (
        'works_for', 'member_of', 'reports_to', 'collaborates_with',
        'located_in', 'part_of', 'owns', 'produces', 'uses',
        'attended', 'about', 'related_to',
        'teaches', 'leads_to', 'contrasts_with', 'caused_by', 'mitigated_by'));

-- Which template was declared for this record, if any. NULL means open-domain
-- extraction, which stays the default: a template is an assertion about what a
-- document is for, and nothing should invent one on the caller's behalf.
ALTER TABLE data_items ADD COLUMN IF NOT EXISTS template text;

-- Which template drew this edge. Recorded on the evidence and on the claim,
-- because it is the thing that makes premeditation reversible: editing a
-- template makes exactly its edges stale, and a template applied in error is
-- retractable because you know precisely which edges came from it.
--
-- It also allows one document to carry more than one graph. A design document
-- is also correspondence; applying both templates is not a conflict, because
-- every edge records the lens that drew it. That is the property that makes
-- this additive rather than exclusive, and it is only available because the
-- column is here rather than inferred from the record.
ALTER TABLE entity_edges ADD COLUMN IF NOT EXISTS template text;
ALTER TABLE entity_facts ADD COLUMN IF NOT EXISTS template text;

-- Searching by template is the point of recording it: "what does the scripture
-- in this project claim leads to what" is a predicate filter and a template
-- filter, and without the index it is a scan of every edge in the org.
CREATE INDEX IF NOT EXISTS entity_edges_template
    ON entity_edges (project_id, template) WHERE template IS NOT NULL;
CREATE INDEX IF NOT EXISTS entity_facts_template
    ON entity_facts (project_id, template) WHERE template IS NOT NULL;
