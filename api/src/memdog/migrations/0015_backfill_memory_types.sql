-- Give every existing project the shipped memory types.
--
-- Types were added after the first projects were created, so those projects had
-- none -- and writes still worked, because routing upserts a memory without
-- consulting the type table. That is the awkward part: the gap was invisible
-- until someone tried to create a memory *deliberately*, which is exactly the
-- path a person uses and a producer does not.
--
-- `default` is the one that matters most: it is where unattached items land.

INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
SELECT 'mty_' || upper(substr(md5(p.project_id || t.name), 1, 26)),
       p.org_id, p.project_id, t.name, t.ttl, t.on_expiry
FROM projects p
CROSS JOIN (VALUES
    ('default',      NULL::int, 'keep_members'),
    ('conversation', 3600,      'orphan_delete'),
    ('session',      86400,     'archive'),
    ('tracing',      259200,    'orphan_delete'),
    ('activity',     7776000,   'orphan_delete')
) AS t(name, ttl, on_expiry)
ON CONFLICT (project_id, name) DO NOTHING;
