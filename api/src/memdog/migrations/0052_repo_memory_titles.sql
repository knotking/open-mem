-- Give the repository memories created before titles existed a title.
--
-- A memory created implicitly by a write has none: a `MemoryRef` carries a key
-- and a type and nothing else, so the raw-graph sibling of every snapshot
-- rendered in every picker as forty characters of hex. Half the repository
-- entries in the memory list were unidentifiable, which is what made the useful
-- ones impossible to find among them.
--
-- Titles are set at creation, so fixing the writer fixed nothing already
-- stored. This is the backfill, and it is a migration rather than a script
-- because a script is a thing somebody has to remember to run against every
-- deployment, and this one has to reach a database on a private address.
--
-- Derived from the key rather than guessed: `owner/repo@sha` is the shape the
-- writer uses, so the title is recoverable from it exactly. A key that does not
-- match that shape is left alone -- a wrong title is worse than none, because
-- none at least falls back to something true.
UPDATE memories
SET title = CASE
        WHEN memory_key LIKE '%/graph' THEN
            split_part(memory_key, '@', 1) || ' @ '
            || left(split_part(split_part(memory_key, '@', 2), '/', 1), 7)
            || ' · raw code graph'
        ELSE
            split_part(memory_key, '@', 1) || ' @ '
            || left(split_part(memory_key, '@', 2), 7)
    END
WHERE type = 'repo_snapshot'
  AND title IS NULL
  AND memory_key IS NOT NULL
  -- `owner/repo@<40 hex>`, optionally `/graph`. Anything else is not a shape
  -- this can name, and is left for a human rather than titled wrongly.
  AND memory_key ~ '^[^/]+/[^/@]+@[0-9a-f]{40}(/graph)?$';
