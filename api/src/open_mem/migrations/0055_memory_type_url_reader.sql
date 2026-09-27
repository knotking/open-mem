-- How a URL in this kind of memory gets read.
--
-- Adding a web page already worked: a `Pending` ref, the fetch worker, the
-- ordinary staircase. URL Context existed too, and only as a *fallback* -- it
-- ran after an HTTP GET had already failed.
--
-- That order has a hole, and it is the reason this column exists rather than a
-- setting somewhere. **A JavaScript-rendered page returns 200 with an empty
-- shell.** The GET does not fail, so the fallback never fires; the record lands
-- `stored` with no readable text, and nothing anywhere reports a problem. It is
-- indistinguishable from a page that was read and had nothing to say. Soft bot
-- walls that answer 200 with an interstitial do the same thing.
--
-- So a type can say "ask the model to read these first". Per type rather than
-- globally, because the inverse trade is real: an HTTP GET returns bytes that
-- are versioned, re-parseable and quotable, and URL Context returns a model's
-- *account* of a page and no bytes at all. Preferring the account everywhere
-- would trade an artifact for an opinion about an artifact, which is the wrong
-- direction for a record store.
--
-- `fetch` is exactly today's behaviour, so every existing type is unchanged by
-- definition and nothing starts spending because this shipped.
ALTER TABLE memory_types
    ADD COLUMN IF NOT EXISTS url_reader text NOT NULL DEFAULT 'fetch';

-- Closed, like every other vocabulary here. An open one stops being
-- aggregatable, and "which of our types read pages with a model" is a question
-- somebody paying the bill will ask.
ALTER TABLE memory_types DROP CONSTRAINT IF EXISTS memory_types_url_reader;
ALTER TABLE memory_types ADD CONSTRAINT memory_types_url_reader
    CHECK (url_reader IN ('fetch', 'context'));
