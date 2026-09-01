-- The other half of push: being told when something comes *due*.
--
-- W10 answers "tell me when this arrives" by matching new writes and never
-- re-scanning. It cannot answer "thirty days before a due date", because
-- **nothing arrives on that day** -- the record showed up months earlier and
-- the only thing that changed is the calendar. Those are two mechanisms, and
-- conflating them is how a standing-query engine quietly becomes a scanner.
--
-- So a second kind on the same table, sharing the matches, the feed and the
-- delivery, and differing in exactly one place: what makes it fire.
--
-- `kind` defaults to `arrival`, so every existing row keeps meaning what it
-- meant. A date rule is bounded by its window rather than by a watermark: it
-- looks at records whose date falls in a range around the offset, which is an
-- index range scan and not a walk of the corpus.
ALTER TABLE standing_queries
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'arrival'
        CHECK (kind IN ('arrival', 'date'));

-- Which date to read. `event_time` is the record's own time and is always
-- present; `ingested_at` is when it arrived, which is what retention ageing
-- means; `metadata.<key>` is whatever the producer put there -- a contract end,
-- a renewal, a statutory deadline.
ALTER TABLE standing_queries ADD COLUMN IF NOT EXISTS date_field text;
-- How far ahead to look. Negative is the past, which is what "older than seven
-- years" is: retention and deadlines are the same mechanism pointed opposite
-- ways, and one column rather than two says so.
ALTER TABLE standing_queries ADD COLUMN IF NOT EXISTS offset_days int;
-- How wide the window is around that offset. Without it a rule fires only on
-- records whose date is exactly N days away to the second, which is to say
-- never.
ALTER TABLE standing_queries ADD COLUMN IF NOT EXISTS window_days int NOT NULL DEFAULT 1;

ALTER TABLE standing_queries DROP CONSTRAINT IF EXISTS standing_queries_date_rule;
ALTER TABLE standing_queries ADD CONSTRAINT standing_queries_date_rule
    CHECK (kind <> 'date' OR (date_field IS NOT NULL AND offset_days IS NOT NULL));
