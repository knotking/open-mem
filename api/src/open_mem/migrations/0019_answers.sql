-- A stored answer is derived from records that had access levels, and is at
-- least as sensitive as the strictest of them. Without this column an answer
-- synthesised from private records would sit in the query log at whatever
-- visibility the log has, which is a way around the ACL on the sources.
ALTER TABLE queries ADD COLUMN IF NOT EXISTS answer_access_level text;

-- `used` means "shown" for a search and "cited" for an answer, so an answer
-- needs a reason the search vocabulary does not have: the passage was retrieved
-- and put in front of the model, which then did not rest anything on it. The
-- reason column stays a closed set -- an open one degrades into free text and
-- stops being aggregatable.
ALTER TABLE query_sources DROP CONSTRAINT IF EXISTS query_sources_excluded_reason_check;
ALTER TABLE query_sources ADD CONSTRAINT query_sources_excluded_reason_check
    CHECK (excluded_reason IN ('acl', 'threshold', 'not_yet_enriched',
                               'retrieved_not_cited'));
