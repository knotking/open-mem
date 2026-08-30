-- The temporal graph: facts, and the evidence for them.
--
-- Until now `entity_edges` was both at once. Its own comment says "the same
-- claim from two documents is two pieces of evidence for one edge, not two
-- edges" -- and then keys on (subject, predicate, object, source_data_id), so
-- it stores a row per *evidence* and none for the claim itself.
--
-- That is fine while an edge has no properties of its own. Validity is a
-- property of the claim: two documents asserting the same thing with different
-- dates would otherwise produce two contradictory windows for one fact, and
-- nothing to hang a supersession on. So the claim gets a row, and `entity_edges`
-- becomes what it always described itself as -- the evidence.
--
-- Two clocks, and conflating them is the classic failure:
--
--   * `valid_from` / `valid_to`  -- when it was true IN THE WORLD
--   * `recorded_at` / `retracted_at` -- when WE LEARNED it
--
-- "As of 15 August, what did we believe was true on 1 February?" needs both.
-- A backfill imported today about last year must not appear in what we believed
-- last month, and one clock cannot express that.
--
-- `cases.md` already argues this for timelines -- "ordered by ingestion time is
-- not merely imprecise, it is wrong in a way that looks right". It just never
-- reached the graph.

CREATE TABLE entity_facts (
    fact_id         text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    subject_id      text NOT NULL REFERENCES entities ON DELETE CASCADE,
    -- Same closed vocabulary as entity_edges, for the same reason: an open one
    -- degrades into unqueryable free text.
    predicate       text NOT NULL CHECK (predicate IN (
                        'works_for', 'member_of', 'reports_to', 'collaborates_with',
                        'located_in', 'part_of', 'owns', 'produces', 'uses',
                        'attended', 'about', 'related_to')),
    object_id       text NOT NULL REFERENCES entities ON DELETE CASCADE,

    -- Valid time. Defaults to the source record's event_time, which is already
    -- NOT NULL on data_items, so there is always an answer.
    valid_from      timestamptz NOT NULL,
    -- NULL means "still true". Written by supersession, never by deletion.
    valid_to        timestamptz,

    -- Transaction time. `recorded_at` is never edited -- editing it would
    -- rewrite what we believed, which is the one thing this table exists to
    -- preserve.
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    -- We no longer believe we ever knew this: the last evidence was erased, or
    -- a person withdrew the assertion. Distinct from valid_to, which says the
    -- fact stopped being true -- a fact can be retracted without ever having
    -- become false.
    retracted_at    timestamptz,
    retracted_reason text,
    superseded_by   text REFERENCES entity_facts(fact_id) ON DELETE SET NULL,

    -- 'derived' means extraction produced it and evidence rows point here.
    -- 'asserted' means a principal stated it outright -- no document, no model,
    -- no cost. The vocabulary is case_members', because "a person said so"
    -- versus "we matched something" is already this codebase's idiom and a
    -- graph that cannot tell them apart is one nobody can rely on for either.
    basis           text NOT NULL CHECK (basis IN ('asserted', 'derived')),
    confidence      real NOT NULL DEFAULT 0.5,
    generator_version text,

    -- Visibility, for asserted facts ONLY.
    --
    -- A derived fact deliberately stores no ACL: its visibility is its
    -- evidence's, computed at query time. This is the same argument memories.md
    -- makes for expiry -- "any stored answer is wrong the moment someone adds
    -- or removes a member" -- and evidence is exactly as mutable. An asserted
    -- fact has no evidence to ask, so it carries its own, in the column names
    -- visibility_sql() already expects.
    owner_id        text REFERENCES users ON DELETE SET NULL,
    access_level    text CHECK (access_level IN
                        ('private', 'org', 'shared', 'restricted', 'public')),
    shared_with     jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- visibility_sql() tests it; a fact is tombstoned rather than dropped.
    deleted_at      timestamptz,
    asserted_by_key_id text,

    CHECK (basis <> 'asserted' OR (owner_id IS NOT NULL AND access_level IS NOT NULL)),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- The real invariant: **at most one OPEN fact per triple**.
--
-- Not "unique per valid_from", which was the first attempt and was wrong in a
-- way a test caught immediately: three documents asserting one claim carry three
-- different event_times, so keying on valid_from made three facts where the
-- evidence for one belonged together.
--
-- A claim true across two separate periods is still two facts -- but the periods
-- are separated by a *closure*, so the second row can only exist once the first
-- has a valid_to. Closed facts are deliberately not constrained: the same claim
-- may have been true, closed, and true again on the same original date.
CREATE UNIQUE INDEX entity_facts_one_open
    ON entity_facts (project_id, subject_id, predicate, object_id)
 WHERE valid_to IS NULL AND retracted_at IS NULL;

-- Open facts by endpoint: what the traversal asks for on every hop.
CREATE INDEX entity_facts_subject_open ON entity_facts (project_id, subject_id)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
CREATE INDEX entity_facts_object_open ON entity_facts (project_id, object_id)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
-- Supersession looks up the open fact for a single-valued predicate.
CREATE INDEX entity_facts_cardinality ON entity_facts (subject_id, predicate)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
-- Point-in-time filtering.
CREATE INDEX entity_facts_valid ON entity_facts (project_id, valid_from, valid_to);

-- Evidence points at the claim it supports.
ALTER TABLE entity_edges ADD COLUMN fact_id text
    REFERENCES entity_facts(fact_id) ON DELETE CASCADE;
CREATE INDEX entity_edges_fact ON entity_edges (fact_id);


-- Backfill: every existing edge group becomes one derived fact.
--
-- A ULID rather than a uuid because ids.py is explicit that ids sort
-- chronologically and carry their creation time, and a backfill that breaks
-- that leaves a table whose ids sort differently from every other. The helper
-- is dropped at the end of this migration -- it is scaffolding, not schema.
CREATE FUNCTION _crockford(value bigint, len int) RETURNS text AS $$
DECLARE
    alphabet text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';  -- no I, L, O, U
    out text := '';
    v bigint := value;
    i int;
BEGIN
    FOR i IN 1..len LOOP
        out := substr(alphabet, (v & 31)::int + 1, 1) || out;
        v := v >> 5;
    END LOOP;
    RETURN out;
END;
$$ LANGUAGE plpgsql;

INSERT INTO entity_facts (
    fact_id, org_id, project_id, subject_id, predicate, object_id,
    valid_from, recorded_at, basis, confidence, generator_version
)
SELECT
    'fct_' || _crockford((extract(epoch FROM min(g.created_at)) * 1000)::bigint, 10)
           || _crockford((random() * 1099511627776)::bigint, 8)
           || _crockford((random() * 1099511627776)::bigint, 8),
    g.org_id, g.project_id, g.subject_id, g.predicate, g.object_id,
    -- Valid time is the world's clock: the earliest event the evidence describes.
    min(d.event_time),
    -- Transaction time is ours: when the first evidence was written.
    min(g.created_at),
    'derived',
    max(g.confidence),
    min(g.generator_version)
  FROM entity_edges g
  JOIN data_items d ON d.data_id = g.source_data_id
 GROUP BY g.org_id, g.project_id, g.subject_id, g.predicate, g.object_id;

UPDATE entity_edges g
   SET fact_id = f.fact_id
  FROM entity_facts f
 WHERE f.project_id = g.project_id
   AND f.subject_id = g.subject_id
   AND f.predicate  = g.predicate
   AND f.object_id  = g.object_id;

DROP FUNCTION _crockford(bigint, int);

-- Every edge is evidence for a claim, now structurally rather than by
-- convention. Without this an edge written by any future path that does not
-- know about facts is silently invisible to the traversal -- which is exactly
-- what happened to two test fixtures the moment the traversal moved.
ALTER TABLE entity_edges ALTER COLUMN fact_id SET NOT NULL;
