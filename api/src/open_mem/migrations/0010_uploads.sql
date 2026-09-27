-- Upload sessions.
--
-- `POST /uploads` is not a write. It grants a *capability* -- permission to put
-- bytes at one specific key -- and the completion is an ordinary write with a
-- Stored ref. Keeping them separate is what stops the write endpoint growing a
-- second admission path.

CREATE TABLE upload_sessions (
    upload_id   text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects ON DELETE CASCADE,
    producer_id text NOT NULL REFERENCES producers ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES users ON DELETE CASCADE,
    external_id text NOT NULL,
    storage_key text NOT NULL,
    mime_type   text,
    -- Declared up front so admission control can refuse before any bytes move.
    size_bytes  bigint,
    -- The signed token is hashed like any other credential: it grants write
    -- access to a storage key, and a leaked upload URL is a write primitive.
    token_hash  bytea NOT NULL,
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'completed', 'expired', 'aborted')),
    received_bytes bigint,
    checksum    text,
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX upload_sessions_producer ON upload_sessions (producer_id, status);
