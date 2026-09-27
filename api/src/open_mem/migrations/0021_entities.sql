-- Entity resolution: graph layer 1, in the record store.
--
-- Deliberately in Postgres rather than a graph database. The traversal has to
-- carry the same visibility predicate as retrieval, and a separate store means
-- either re-implementing that rule or post-filtering -- and post-filtering a
-- graph leaks structure, because "three nodes are hidden here" discloses that
-- they exist. Erasure has the same shape: a second store is a second place
-- personal data hides from verify_erasure.

CREATE TABLE entities (
    entity_id       text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    -- A closed vocabulary. An open one degrades into unqueryable free text,
    -- and the whole value of a typed node is that you can ask for all of them.
    type            text NOT NULL CHECK (type IN
                    ('person', 'organization', 'location', 'product',
                     'event', 'topic', 'other')),
    -- What to show. The most complete surface form seen, not the first.
    display_name    text NOT NULL,
    -- What to match on: casefolded, punctuation-stripped, whitespace-collapsed.
    normalized_name text NOT NULL,
    -- Strong identifiers -- an email, a URL, a handle. Two mentions sharing one
    -- are the same thing; two sharing only a name might not be.
    identifiers     text[] NOT NULL DEFAULT '{}',
    mention_count   int NOT NULL DEFAULT 0,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    -- Set when this entity was merged into another. The row is kept rather
    -- than deleted so the merge can be undone and so old references still
    -- resolve -- a merge is a judgement, and judgements are sometimes wrong.
    merged_into     text REFERENCES entities ON DELETE SET NULL,
    UNIQUE (project_id, type, normalized_name)
);

CREATE INDEX ON entities (project_id, type);
CREATE INDEX ON entities (project_id) WHERE merged_into IS NULL;
CREATE INDEX ON entities USING gin (identifiers);

-- Every mention is kept with the record it came from. This is what makes a
-- resolution auditable and reversible: the evidence survives, not just the
-- verdict. Without it, an entity is an assertion nobody can check.
CREATE TABLE entity_mentions (
    mention_id      text PRIMARY KEY,
    entity_id       text NOT NULL REFERENCES entities ON DELETE CASCADE,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    org_id          text NOT NULL,
    project_id      text NOT NULL,
    -- The surface form as written. "Priya", "Priya Raman" and "P. Raman" are
    -- all evidence about the same person and all worth keeping.
    surface         text NOT NULL,
    -- Why this mention resolved to this entity: identifier, exact_name, or
    -- new. A resolution nobody can explain is one nobody can correct.
    resolved_by     text NOT NULL CHECK (resolved_by IN
                    ('identifier', 'exact_name', 'new', 'manual')),
    generator_version text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, data_id, surface)
);

CREATE INDEX ON entity_mentions (data_id);
CREATE INDEX ON entity_mentions (entity_id);

-- Merges are recorded rather than applied destructively, so "these two are the
-- same person" can be taken back when it turns out they are twins.
CREATE TABLE entity_merges (
    merge_id        text PRIMARY KEY,
    org_id          text NOT NULL,
    source_id       text NOT NULL,
    target_id       text NOT NULL,
    reason          text,
    merged_by       text,
    merged_at       timestamptz NOT NULL DEFAULT now(),
    undone_at       timestamptz
);

CREATE INDEX ON entity_merges (target_id) WHERE undone_at IS NULL;
