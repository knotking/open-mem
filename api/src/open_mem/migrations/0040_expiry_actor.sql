-- Expiry acts, and the audit record has to be able to say so.
--
-- `actor_mode` admitted 'user', 'platform' and 'crawler'. The retention sweep
-- is none of those: it runs unattended like the crawler, but it *deletes*, and
-- filing that under 'platform' would put an operator's fingerprint on an
-- erasure nobody performed. The one question an erasure record has to answer is
-- who did this, and "the policy did, on this schedule" is a real answer that
-- was not previously sayable.
--
-- Additive, and the constraint is replaced rather than edited in place --
-- 0020 did the same for 'crawler', and a migration already applied is never
-- rewritten.
ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_actor_mode_check;
ALTER TABLE audit_events ADD CONSTRAINT audit_events_actor_mode_check
    CHECK (actor_mode IN ('user', 'platform', 'crawler', 'expiry'));
