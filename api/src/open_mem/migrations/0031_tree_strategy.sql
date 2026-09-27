-- A fourth crawl strategy: folder recursion.
--
-- The `http` strategy lists one folder because that is one request. A document
-- library is a tree, and "everything under this folder" was the thing the Drive
-- and SharePoint catalog entries had to admit they could not do.
--
-- Kept as a closed CHECK rather than opened to free text for the same reason it
-- was closed originally: a strategy name that does not resolve is a crawler
-- that stores fine and fails at 3am.

ALTER TABLE crawlers DROP CONSTRAINT IF EXISTS crawlers_strategy_check;
ALTER TABLE crawlers ADD CONSTRAINT crawlers_strategy_check
    CHECK (strategy IN ('http', 'feed', 'traverse', 'tree'));
