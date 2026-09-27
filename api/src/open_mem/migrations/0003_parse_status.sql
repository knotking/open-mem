-- Why an item's bytes are not text.
--
-- "Nothing is rejected" only means something if the reason survives. An item
-- that stored but never became searchable is otherwise indistinguishable from
-- one that is merely behind -- the same confusion the reconciler exists to
-- avoid, one layer down.

ALTER TABLE data_items ADD COLUMN parse_status text
    CHECK (parse_status IN ('parsed', 'unsupported', 'encrypted', 'malformed',
                            'needs_model', 'truncated'));
ALTER TABLE data_items ADD COLUMN parse_detail jsonb;

-- The operational question this answers: what is in the corpus that we are not
-- reading, and why?
CREATE INDEX data_items_parse_status ON data_items (project_id, parse_status)
    WHERE parse_status IS NOT NULL AND parse_status <> 'parsed';
