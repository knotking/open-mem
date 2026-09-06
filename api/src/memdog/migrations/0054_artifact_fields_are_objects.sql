-- Artifacts whose `fields` is a JSON *string* rather than a JSON object.
--
-- The pool sets a jsonb codec whose encoder is `json.dumps`, and the insert
-- called `json.dumps` as well. Encoding twice stores `'{"findings": [...]}'` --
-- a jsonb string whose content happens to be JSON -- so every reader doing
-- `fields.findings` got nothing.
--
-- It fails in the quietest way available: the artifact exists, its title and
-- summary render normally, and only the structured half is missing. A code
-- review with findings and one with none look identical, which is the exact
-- distinction that half of `fields` exists to carry.
--
-- Keyed on `jsonb_typeof`, so it repairs precisely the affected rows and is a
-- no-op on every correctly written one. `#>> '{}'` extracts a jsonb string's
-- text; casting that back parses the JSON that was inside it.
UPDATE artifacts
SET fields = (fields #>> '{}')::jsonb
WHERE jsonb_typeof(fields) = 'string'
  -- Only where the text really is an object. A generator that legitimately
  -- stored a bare string would be corrupted by the cast, and there is no
  -- reading of this column where guessing beats leaving it alone.
  AND left(ltrim(fields #>> '{}'), 1) = '{';
