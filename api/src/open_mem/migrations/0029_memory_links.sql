-- Finish `memory_links`, which has been declared and unreachable since the
-- memories migration shipped.
--
-- The table was right and nothing wrote to it: no function, no endpoint, no
-- reader. A memory could never be said to continue another, or to be derived
-- from the conversations it compressed -- which is most of what the relations
-- were enumerated for.
--
-- Two columns from the specification were missing, and they are the ones that
-- carry the distinction that matters. A link a person declared and a link an
-- agent inferred are different claims, exactly as case membership is: the first
-- is a statement someone will stand behind, the second is a guess that should
-- be rendered subordinate and excluded from a count. Storing both in a table
-- that cannot tell them apart is how an inference quietly becomes a fact.

ALTER TABLE memory_links
    ADD COLUMN created_by text NOT NULL DEFAULT 'explicit'
        CHECK (created_by IN ('explicit', 'routed', 'agent')),
    -- Only meaningful for a derived link. An explicit one is not 80% true.
    ADD COLUMN confidence real,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();

-- Traversal goes both ways: "what is derived from this?" and "what is this
-- derived from?" are both asked, and a link is only useful if the reverse
-- lookup is as cheap as the forward one.
CREATE INDEX ON memory_links (to_memory);
