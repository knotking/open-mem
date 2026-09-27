-- Invites, and the reason registration is closed by default.
--
-- Until now anyone who could authenticate got a `users` row. They could do
-- nothing with it -- membership is what grants capability, and there was none --
-- but the row appeared, and an account-creation side effect nobody asked for is
-- a poor default for a system holding other people's records.
--
-- The alternative is not "add a flag later". Opening registration and closing it
-- afterwards means **everyone who signed up in between is already inside**, and
-- the closing does not remove them. So the default is closed from the first
-- release, and an invite is how the second user arrives.
--
-- **An invite is a bearer credential, and gets credential treatment.** It grants
-- org membership at a stated role, which is exactly what a credential does, so
-- it is stored the way `api_keys` are stored: the token is hashed, only a
-- display prefix is kept in the clear, and the token itself is shown once at
-- creation and never again.
--
-- Two properties are worth more than they look:
--
--   * **Single-use.** Redemption sets `redeemed_at` under a conditional update,
--     so two people racing the same link produce one member and one refusal
--     rather than two members.
--   * **Email-bound by default.** An unscoped link is transferable -- forwarded,
--     pasted into a channel, screenshotted -- and whoever redeems it becomes a
--     member. Binding it to an address makes a forwarded link fail, which is
--     what the person sending it actually intended.

CREATE TABLE invites (
    invite_id   text PRIMARY KEY,
    org_id      text NOT NULL REFERENCES organizations ON DELETE CASCADE,

    -- Hashed like an API key, and for the same reason: this is a bearer token,
    -- so a database read must not yield a usable credential. The prefix is what
    -- an admin sees in a list and what revocation names.
    prefix      text NOT NULL UNIQUE,
    token_hash  bytea NOT NULL,

    -- The membership this grants, decided by the inviter and not by the
    -- redeemer. A redeemer who could name their own role would be an
    -- unauthenticated privilege-escalation endpoint.
    role        text NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),

    -- NULL means transferable to anyone holding the link. Permitted, because a
    -- deployment may genuinely want a link it can hand around, but never the
    -- default -- the caller has to ask for it.
    email       text,

    created_by  text REFERENCES users ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,

    -- Who actually walked through the door, which is a different question from
    -- who was invited: an invite forwarded to somebody else answers only this
    -- one, and that is precisely when it matters.
    redeemed_at timestamptz,
    redeemed_by text REFERENCES users ON DELETE SET NULL,
    revoked_at  timestamptz
);

-- Listing an org's outstanding invites, newest first.
CREATE INDEX ON invites (org_id, created_at DESC);
-- Registration checks "is there a live invite for this address?" on every new
-- identity, so it is worth an index of its own.
CREATE INDEX ON invites (lower(email))
    WHERE email IS NOT NULL AND redeemed_at IS NULL AND revoked_at IS NULL;
