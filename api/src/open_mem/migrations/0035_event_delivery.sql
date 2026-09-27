-- Outbound delivery. open-mem has never had one: `webhooks.py` is inbound only,
-- by its own docstring, and `/producers/{id}/test-delivery` signs a payload and
-- posts it to open-mem's *own* receive path, which is a self-test.
--
-- Recording an event and delivering it are two commitments, and conflating them
-- is how a notification system starts losing things. `observed_events` is the
-- record; a subscriber that is down loses nothing, because the row is already
-- there and the poll cursor still reaches it.

CREATE TABLE event_subscriptions (
    subscription_id text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects ON DELETE CASCADE,
    -- NULL means every alert in the project, including ones created later.
    alert_id    text REFERENCES alerts ON DELETE CASCADE,
    url         text NOT NULL,
    -- Whose rights the delivery is filtered by. Not optional: a subscription
    -- with no owner is a notification channel with no access control, and
    -- notification is already the one side channel around every other check.
    owner_id    text NOT NULL REFERENCES users ON DELETE CASCADE,
    -- Encrypted, and never readable back through the API. A secret a
    -- `config:write` credential can fetch is a secret shared with everyone
    -- holding one.
    signing_secret_ct bytea NOT NULL,
    -- Rotation needs an overlap or it is an outage for everything in flight --
    -- the same reason producers grew this pair in 0018.
    previous_signing_secret_ct bytea,
    signing_secret_rotated_at timestamptz,
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX event_subscriptions_project ON event_subscriptions (project_id)
    WHERE enabled;

CREATE TABLE event_deliveries (
    delivery_id text PRIMARY KEY,
    subscription_id text NOT NULL REFERENCES event_subscriptions ON DELETE CASCADE,
    event_id    text NOT NULL REFERENCES observed_events ON DELETE CASCADE,
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'delivered', 'failed', 'dead')),
    attempts    int NOT NULL DEFAULT 0,
    response_status int,
    last_error  text,
    -- Nothing wakes on its own. The alert tick honours this, which is why the
    -- tick is not optional on a deployment that scales to zero.
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    -- At-least-once, deduplicated at the source: a re-dispatch after a crash
    -- must not create a second delivery of the same event.
    UNIQUE (subscription_id, event_id)
);

CREATE INDEX event_deliveries_owed ON event_deliveries (next_attempt_at)
    WHERE status = 'pending';
-- The "is my endpoint healthy" query, and the dead letters, each one lookup.
CREATE INDEX event_deliveries_subscription ON event_deliveries (subscription_id, created_at DESC);
CREATE INDEX event_deliveries_dead ON event_deliveries (subscription_id) WHERE status = 'dead';
