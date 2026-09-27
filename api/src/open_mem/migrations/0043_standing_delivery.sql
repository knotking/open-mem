-- Push for standing queries, through the sender that already exists.
--
-- The alternative was a second delivery pipeline, and a second one would need
-- its own signing, its own backoff, its own dead-letter rule and its own
-- SSRF check -- four controls that are only worth anything if they are the
-- same four everywhere. So the subject of a delivery becomes one of two things
-- rather than the sender becoming generic in the abstract.
--
-- Two nullable FKs with a CHECK rather than a polymorphic `(kind, id)` pair:
-- the foreign keys are what make a delivery disappear when its subject is
-- erased, and erasure is the one path where a dangling reference would mean a
-- record the platform promised was gone being POSTed to a URL.
ALTER TABLE event_subscriptions
    ADD COLUMN IF NOT EXISTS standing_query_id text
        REFERENCES standing_queries ON DELETE CASCADE;

-- `alert_id IS NULL` has always meant "every alert in this project". That must
-- not silently start meaning "and every standing query too" -- a subscriber
-- registered last month would begin receiving a kind of payload it has never
-- seen. `kind` says which family a subscription is for, and the default keeps
-- every existing row meaning exactly what it meant.
ALTER TABLE event_subscriptions
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'alert'
        CHECK (kind IN ('alert', 'standing'));

ALTER TABLE event_deliveries ALTER COLUMN event_id DROP NOT NULL;
ALTER TABLE event_deliveries
    ADD COLUMN IF NOT EXISTS match_id text
        REFERENCES standing_matches ON DELETE CASCADE;
ALTER TABLE event_deliveries DROP CONSTRAINT IF EXISTS event_deliveries_subject;
ALTER TABLE event_deliveries ADD CONSTRAINT event_deliveries_subject
    CHECK ((event_id IS NULL) <> (match_id IS NULL));

-- The same at-least-once deduplication the event side has: a re-dispatch after
-- a crash must not send a second copy of one match.
CREATE UNIQUE INDEX IF NOT EXISTS event_deliveries_match
    ON event_deliveries (subscription_id, match_id) WHERE match_id IS NOT NULL;
