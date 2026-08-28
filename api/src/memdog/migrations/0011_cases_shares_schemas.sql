-- Cases, shares, normalization schemas and agent configs.
--
-- A case is a **subject** -- what is this about? -- as opposed to a memory,
-- which is a lifecycle. A conversation expires; a patient does not.

CREATE TABLE cases (
    case_id     text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects ON DELETE CASCADE,
    case_type   text NOT NULL,
    external_id text NOT NULL,
    title       text,
    identifiers text[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    UNIQUE (project_id, case_type, external_id)
);

CREATE TABLE case_members (
    case_id     text NOT NULL REFERENCES cases ON DELETE CASCADE,
    data_id     text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    -- Asserted means a person or a producer said so. Inferred means we matched
    -- an identifier. A timeline that cannot tell them apart is a timeline that
    -- silently includes someone else's records.
    basis       text NOT NULL CHECK (basis IN ('asserted', 'inferred')),
    confidence  double precision,
    matched_on  text,
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (case_id, data_id)
);

CREATE INDEX case_members_data ON case_members (data_id);

-- Genuinely external sharing: the feature most likely to cause an accidental
-- disclosure, so it carries controls the others do not.
CREATE TABLE share_links (
    share_id    text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects ON DELETE CASCADE,
    data_id     text REFERENCES data_items ON DELETE CASCADE,
    case_id     text REFERENCES cases ON DELETE CASCADE,
    token_hash  bytea NOT NULL,
    prefix      text NOT NULL UNIQUE,
    created_by  text NOT NULL REFERENCES users ON DELETE RESTRICT,
    -- Required, not optional: a link with no expiry is a permanent disclosure
    -- nobody revisits.
    expires_at  timestamptz NOT NULL,
    password_hash bytea,
    revoked_at  timestamptz,
    access_count int NOT NULL DEFAULT 0,
    last_accessed_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK ((data_id IS NOT NULL) <> (case_id IS NOT NULL))
);

-- The view that catches the mistake made six months ago.
CREATE INDEX share_links_live ON share_links (org_id, expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE normalization_schemas (
    schema_id   text PRIMARY KEY,
    org_id      text REFERENCES organizations ON DELETE CASCADE,
    project_id  text REFERENCES projects ON DELETE CASCADE,
    target_type text NOT NULL,
    version     int NOT NULL DEFAULT 1,
    fields      jsonb NOT NULL,
    mapping     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, target_type, version)
);

CREATE TABLE normalized_records (
    data_id     text PRIMARY KEY REFERENCES data_items ON DELETE CASCADE,
    target_type text NOT NULL,
    schema_version int NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',
    identifiers text[] NOT NULL DEFAULT '{}',
    -- A normalization failure lands the record raw with a reason rather than
    -- rejecting the write, and stays retryable.
    status      text NOT NULL CHECK (status IN ('ok', 'failed')),
    failure_reason text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX normalized_records_type ON normalized_records (target_type, data_id);

-- Extraction prompts, overridable per org and project. The defaults ship; an
-- override is opting *out* of a default rather than filling in a blank.
CREATE TABLE agent_configs (
    config_id   text PRIMARY KEY,
    org_id      text REFERENCES organizations ON DELETE CASCADE,
    project_id  text REFERENCES projects ON DELETE CASCADE,
    data_type   text NOT NULL,
    prompt      text,
    flags       jsonb NOT NULL DEFAULT '{}',
    locked      boolean NOT NULL DEFAULT false,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, project_id, data_type)
);
