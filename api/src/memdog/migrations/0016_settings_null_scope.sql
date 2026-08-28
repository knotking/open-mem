-- Platform-scope settings have a NULL scope_id, and SQL treats NULLs as
-- distinct -- so `UNIQUE (scope, scope_id, key)` never fired for them. Two
-- consequences, one silent and one loud:
--
--   * the same platform setting could be inserted repeatedly, and the resolver
--     would return whichever row it happened to read;
--   * an upsert targeting that constraint fell through to the primary key and
--     raised, which is how this was noticed at all.
--
-- Postgres 15 added NULLS NOT DISTINCT for exactly this shape.

DELETE FROM settings a USING settings b
WHERE a.ctid < b.ctid
  AND a.scope = b.scope AND a.key = b.key
  AND a.scope_id IS NOT DISTINCT FROM b.scope_id;

ALTER TABLE settings DROP CONSTRAINT settings_scope_scope_id_key_key;
ALTER TABLE settings ADD CONSTRAINT settings_scope_key
    UNIQUE NULLS NOT DISTINCT (scope, scope_id, key);
