-- Inbound webhook delivery.
--
-- `POST /webhooks/{whk_id}` is a **translator in front of** the write API, not a
-- second one. It accepts whatever shape a provider sends, maps it onto items,
-- and calls the same write path -- so admission control, ACL assignment, memory
-- routing, events and audit all happen exactly once, in one place.

-- Providers retry. Aggressively. Recording the delivery id is what turns a
-- duplicate POST into a no-op rather than a duplicate item, and keeps a record
-- of what actually arrived when a provider claims it sent something.
CREATE TABLE webhook_deliveries (
    delivery_id  text PRIMARY KEY,
    producer_id  text NOT NULL REFERENCES producers ON DELETE CASCADE,
    org_id       text NOT NULL,
    -- The provider's own id for this delivery, when it sends one. Unique per
    -- producer so a replay is detected rather than re-ingested.
    external_delivery_id text,
    signature_verified boolean NOT NULL DEFAULT false,
    status       text NOT NULL
                 CHECK (status IN ('accepted', 'duplicate', 'rejected', 'dropped')),
    reason       text,
    items        int NOT NULL DEFAULT 0,
    payload_bytes int,
    received_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (producer_id, external_delivery_id)
);

CREATE INDEX webhook_deliveries_producer ON webhook_deliveries (producer_id, received_at DESC);

-- How a provider's payload becomes items. Kept on the producer because it is a
-- property of *this* integration, not of the platform: two Slack workspaces can
-- map differently without either being wrong.
ALTER TABLE producers ADD COLUMN inbound_mapping jsonb NOT NULL DEFAULT '{}';
-- Rotating a signing secret needs an overlap window, or rotation means an
-- outage for every delivery in flight.
ALTER TABLE producers ADD COLUMN previous_signing_secret_ct bytea;
ALTER TABLE producers ADD COLUMN signing_secret_rotated_at timestamptz;
