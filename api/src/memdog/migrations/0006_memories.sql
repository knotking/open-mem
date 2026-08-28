-- Memories: typed containers with a lifecycle.
--
-- A memory answers "how long does this matter?". A case answers "what is this
-- about?". A conversation expires; a patient does not.

CREATE TABLE memory_types (
    type_id     text PRIMARY KEY,
    org_id      text REFERENCES organizations ON DELETE CASCADE,
    project_id  text REFERENCES projects ON DELETE CASCADE,
    name        text NOT NULL,
    -- Three fields, deliberately. Not a taxonomy: earlier drafts shipped ten
    -- types with semantics baked into each, and nearly all of it was
    -- expressible as a TTL plus an expiry policy.
    ttl_seconds int,                                  -- null = never expires
    on_expiry   text NOT NULL DEFAULT 'orphan_delete'
                CHECK (on_expiry IN ('orphan_delete', 'keep_members', 'archive')),
    locked      boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE TABLE memories (
    memory_id   text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects ON DELETE CASCADE,
    -- Mutable, and never encoded in the identifier: re-typing a memory must not
    -- mean re-keying it (FR-MEMT-13).
    type        text NOT NULL,
    memory_key  text,
    title       text,
    owner_id    text REFERENCES users ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    -- The natural key that makes routing work without any session state: a
    -- producer writing many messages from one thread collects them into one
    -- memory without pre-creating anything.
    UNIQUE (project_id, type, memory_key)
);

CREATE INDEX memories_project ON memories (project_id, type);

CREATE TABLE memory_members (
    memory_id   text NOT NULL REFERENCES memories ON DELETE CASCADE,
    data_id     text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    added_at    timestamptz NOT NULL DEFAULT now(),
    -- "This item is in this memory because a rule put it there" and "because a
    -- person put it there" are different facts with different trust.
    added_by    text NOT NULL CHECK (added_by IN ('explicit', 'routed', 'agent')),
    PRIMARY KEY (memory_id, data_id)
);

-- The reverse lookup that answers "why is this still here?"
CREATE INDEX memory_members_data ON memory_members (data_id);

CREATE TABLE memory_links (
    from_memory text NOT NULL REFERENCES memories ON DELETE CASCADE,
    to_memory   text NOT NULL REFERENCES memories ON DELETE CASCADE,
    relation    text NOT NULL
                CHECK (relation IN ('part_of', 'derived_from', 'about',
                                    'continues', 'supersedes')),
    PRIMARY KEY (from_memory, to_memory, relation)
);

-- NOTE the absence: there is no `expires_at` on memory_members and none on
-- data_items. Effective expiry is the MAXIMUM ttl across an item's
-- memberships, and membership is mutable -- so any stored answer is wrong the
-- moment someone adds or removes a member. Adding the column back would
-- silently break orphan_delete (FR-SCH-5).
