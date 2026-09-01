-- `data_items.metadata` was stored as a JSON string containing JSON.
--
-- The write path serialised it and the pool's jsonb codec serialised it again,
-- so every row holds a `jsonb` of type `string` rather than `object`. It read
-- back correctly in Python -- the round trip through `json.loads` undid it --
-- which is exactly why it survived: the only thing that failed was SQL, and
-- until a date rule needed `metadata ->> 'deadline'` nothing asked SQL.
--
-- Repaired in place: unwrap the string and re-parse it. Idempotent by its own
-- WHERE clause, and it touches only rows that are wrong.
--
-- Rows whose text is not valid JSON are left alone rather than dropped -- there
-- should be none, and losing a producer's metadata to a repair would be worse
-- than leaving one row odd.
UPDATE data_items
   SET metadata = (metadata #>> '{}')::jsonb
 WHERE jsonb_typeof(metadata) = 'string'
   AND (metadata #>> '{}') IS NOT NULL
   AND left(ltrim(metadata #>> '{}'), 1) = '{';
