-- Phase 1 spine. Every column here exists because it cannot be backfilled.
-- {{EMBED_DIM}} is substituted at migration time from settings.embed_dim.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------- tenancy

CREATE TABLE organizations (
    org_id          text PRIMARY KEY,
    name            text NOT NULL,
    -- FR-ACC-4: public sharing is disabled at org level until an admin enables it.
    allow_public_sharing boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    project_id      text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    name            text NOT NULL,
    -- FR-SCH-16: answer-text storage policy, default metadata-only.
    answer_storage  text NOT NULL DEFAULT 'metadata'
                    CHECK (answer_storage IN ('none', 'metadata', 'full')),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    user_id         text PRIMARY KEY,
    email           text UNIQUE,
    display_name    text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Many ways to authenticate, one canonical user. Firebase/SAML arrive as rows,
-- not as a migration.
CREATE TABLE identities (
    identity_id     text PRIMARY KEY,
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    provider        text NOT NULL CHECK (provider IN ('local', 'apikey', 'firebase', 'saml')),
    external_id     text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);

CREATE TABLE memberships (
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, org_id)
);

CREATE TABLE groups (
    group_id        text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    name            text NOT NULL,
    managed_by      text NOT NULL DEFAULT 'manual'
                    CHECK (managed_by IN ('manual', 'scim', 'idp_claim')),
    UNIQUE (org_id, name)
);

CREATE TABLE group_members (
    group_id        text NOT NULL REFERENCES groups ON DELETE CASCADE,
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

-- ---------------------------------------------------------- credentials

-- SHA-256, not Argon2: high-entropy tokens gain nothing from a slow KDF and
-- pay for it on every request.
CREATE TABLE api_keys (
    key_id          text PRIMARY KEY,
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text REFERENCES projects ON DELETE CASCADE,
    name            text NOT NULL DEFAULT '',
    prefix          text NOT NULL UNIQUE,
    key_hash        bytea NOT NULL,
    capabilities    text[] NOT NULL DEFAULT '{}',
    expires_at      timestamptz,
    last_used_at    timestamptz,
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON api_keys (user_id);

-- An authorised link to a source. scope decides the ACL at write time.
CREATE TABLE connections (
    connection_id   text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    provider        text NOT NULL,
    scope           text NOT NULL CHECK (scope IN ('personal', 'shared')),
    -- envelope-encrypted; never plaintext, and decryption fails closed
    credential_ct   bytea,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Nothing writes anonymously.
CREATE TABLE producers (
    producer_id     text PRIMARY KEY,
    type            text NOT NULL CHECK (type IN ('webhook', 'crawler', 'client', 'upload', 'agent')),
    user_id         text NOT NULL REFERENCES users ON DELETE CASCADE,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    connection_id   text REFERENCES connections ON DELETE SET NULL,
    status          text NOT NULL DEFAULT 'enabled'
                    CHECK (status IN ('draft', 'enabled', 'disabled')),
    inbound_auth    text NOT NULL DEFAULT 'api_key'
                    CHECK (inbound_auth IN ('api_key', 'signature', 'url_secret', 'none')),
    api_key_id      text REFERENCES api_keys ON DELETE SET NULL,
    signing_secret_ct bytea,
    defaults        jsonb NOT NULL DEFAULT '{}',
    policy          jsonb NOT NULL DEFAULT '{}',
    last_item_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON producers (project_id);

-- ------------------------------------------------------------------ data

CREATE TABLE data_items (
    data_id         text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    producer_id     text NOT NULL REFERENCES producers ON DELETE RESTRICT,
    connection_id   text REFERENCES connections ON DELETE SET NULL,
    owner_id        text NOT NULL REFERENCES users ON DELETE RESTRICT,
    external_id     text NOT NULL,

    access_level    text NOT NULL
                    CHECK (access_level IN ('private', 'org', 'shared', 'restricted', 'public')),
    shared_with     jsonb NOT NULL DEFAULT '[]',

    content_text    text,
    storage_ref     text,
    pending_ref     jsonb,

    -- FR-SCH-2: derived, never stored by hand. Drift was the original defect.
    is_downloaded   boolean GENERATED ALWAYS AS
                    (content_text IS NOT NULL OR storage_ref IS NOT NULL) STORED,

    mime_type       text,           -- FR-SCH-9: server-sniffed, authoritative
    source_type     text,           -- hint only; it can lie
    data_type       text,           -- output of the classification cascade
    classified_by_layer int,
    size_bytes      bigint,
    checksum        text,

    -- FR-SCH-3: both required. One column cannot carry both.
    event_time      timestamptz NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now(),

    state           text NOT NULL DEFAULT 'stored'
                    CHECK (state IN ('stored', 'searchable', 'enriched')),
    identifiers     text[] NOT NULL DEFAULT '{}',
    tags            text[] NOT NULL DEFAULT '{}',

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    purged_at       timestamptz,

    -- exactly one ContentRef case
    CONSTRAINT one_content_ref CHECK (
        (content_text IS NOT NULL)::int
      + (storage_ref  IS NOT NULL)::int
      + (pending_ref  IS NOT NULL)::int = 1
    ),
    -- restricted excludes the owner, so it cannot be the owner-only case
    CONSTRAINT restricted_needs_principals CHECK (
        access_level <> 'restricted' OR jsonb_array_length(shared_with) > 0
    )
);

-- Upsert on the caller's natural key: dedupe within a source, never across.
CREATE UNIQUE INDEX data_items_natural_key
    ON data_items (project_id, producer_id, external_id);
CREATE INDEX data_items_scoped_timeline
    ON data_items (project_id, event_time DESC);
CREATE INDEX data_items_owner ON data_items (owner_id);
CREATE INDEX data_items_checksum ON data_items (checksum) WHERE checksum IS NOT NULL;

CREATE TABLE chunks (
    chunk_id        text PRIMARY KEY,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    ordinal         int NOT NULL,
    text            text NOT NULL,
    span_start      int NOT NULL,
    span_end        int NOT NULL,
    -- the lexical index is a column, not a table
    ts              tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_id, ordinal)
);

CREATE INDEX chunks_ts ON chunks USING gin (ts);
CREATE INDEX chunks_data ON chunks (data_id);

-- The immutable registry. generator_version is a fingerprint, not a number.
CREATE TABLE generators (
    generator_version text PRIMARY KEY,
    purpose         text NOT NULL,
    model_id        text NOT NULL,
    spec            jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- FR-SCH-4: model_id is the single most important column in this schema.
CREATE TABLE embeddings (
    embedding_id    text PRIMARY KEY,
    chunk_id        text NOT NULL REFERENCES chunks ON DELETE CASCADE,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    model_id        text NOT NULL,
    dim             int NOT NULL,
    generator_version text NOT NULL REFERENCES generators,
    embedding       vector({{EMBED_DIM}}) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chunk_id, model_id)
);

CREATE INDEX embeddings_vec ON embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX embeddings_model ON embeddings (model_id);
CREATE INDEX embeddings_data ON embeddings (data_id);

-- ------------------------------------------------------- derived layer

CREATE TABLE artifacts (
    artifact_id     text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    kind            text NOT NULL,
    -- core envelope as columns: every list view and citation reads these
    title           text,
    description     text,
    summary         text,
    keywords        text[] NOT NULL DEFAULT '{}',
    language        text,
    fields          jsonb NOT NULL DEFAULT '{}',
    model_id        text NOT NULL,
    generator_version text NOT NULL REFERENCES generators,
    served_by_model text NOT NULL,
    fallback_depth  int NOT NULL DEFAULT 0,
    -- carries the ACL of its most restrictive source
    access_level    text NOT NULL,
    shared_with     jsonb NOT NULL DEFAULT '[]',
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- FR-SCH-6: a join table, because erasure asks "which artifacts absorbed this?"
CREATE TABLE artifact_sources (
    artifact_id     text NOT NULL REFERENCES artifacts ON DELETE CASCADE,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    span_start      int,
    span_end        int,
    PRIMARY KEY (artifact_id, data_id, span_start, span_end)
);

CREATE INDEX artifact_sources_data ON artifact_sources (data_id);

-- ------------------------------------------------------------ model config

CREATE TABLE engines (
    engine_id       text PRIMARY KEY,
    org_id          text REFERENCES organizations ON DELETE CASCADE,
    provider        text NOT NULL,
    base_url        text,
    credential_ct   bytea,
    enabled         boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE model_assignments (
    assignment_id   text PRIMARY KEY,
    org_id          text REFERENCES organizations ON DELETE CASCADE,
    purpose         text NOT NULL,
    data_type       text NOT NULL DEFAULT '*',
    scope           text NOT NULL DEFAULT 'org',
    engine_id       text NOT NULL REFERENCES engines ON DELETE RESTRICT,
    model_id        text NOT NULL,
    UNIQUE (org_id, purpose, data_type, scope)
);

-- ------------------------------------------------------------ governance

-- Everything that is not a read. Low volume, never purged.
CREATE TABLE audit_events (
    event_id        text PRIMARY KEY,
    org_id          text NOT NULL,
    project_id      text,
    actor_user_id   text,
    actor_key_id    text,
    actor_mode      text NOT NULL DEFAULT 'user'
                    CHECK (actor_mode IN ('user', 'platform')),
    action          text NOT NULL,
    target_type     text,
    target_id       text,
    detail          jsonb NOT NULL DEFAULT '{}',
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_org_at ON audit_events (org_id, at DESC);
CREATE INDEX audit_events_target ON audit_events (target_id);

-- FR-SCH-7: append-only, partitioned, and it must survive the deletion of what
-- it describes -- hence no foreign keys.
CREATE TABLE access_log (
    access_id       text NOT NULL,
    org_id          text NOT NULL,
    project_id      text,
    user_id         text,
    key_id          text,
    principal       text,
    action          text NOT NULL,
    data_id         text,
    query_id        text,
    detail          jsonb NOT NULL DEFAULT '{}',
    at              timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (at);

CREATE TABLE access_log_default PARTITION OF access_log DEFAULT;
CREATE INDEX access_log_org_at ON access_log (org_id, at DESC);

CREATE TABLE queries (
    query_id        text PRIMARY KEY,
    user_id         text,
    org_id          text NOT NULL,
    project_id      text NOT NULL,
    question        text NOT NULL,
    answer          text,               -- nullable by policy, not by accident
    model_id        text,
    generator_version text,
    served_by_model text,
    token_cost      bigint,
    latency_ms      int,
    asked_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE query_sources (
    query_id        text NOT NULL REFERENCES queries ON DELETE CASCADE,
    data_id         text NOT NULL,
    rank            int NOT NULL,
    score           double precision,
    used            boolean NOT NULL,
    excluded_reason text CHECK (excluded_reason IN ('acl', 'threshold', 'not_yet_enriched')),
    PRIMARY KEY (query_id, data_id)
);

CREATE INDEX query_sources_data ON query_sources (data_id);

-- Replay protection for the write endpoint.
CREATE TABLE idempotency_keys (
    key             text NOT NULL,
    producer_id     text NOT NULL REFERENCES producers ON DELETE CASCADE,
    request_hash    text NOT NULL,
    response        jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (producer_id, key)
);
