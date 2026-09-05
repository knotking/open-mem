-- The public demo's meter.
--
-- An unauthenticated endpoint that makes a model call per request is an open
-- tap on somebody's bill, and the only honest way to open one is to count what
-- comes out of it. Two limits ride on this table: a per-IP hourly rate so one
-- visitor cannot monopolise it, and a global daily cap so the whole surface
-- stops rather than degrades when the day's budget is gone.
--
-- The cap is deliberately a hard stop. A throttle still spends -- it just
-- spends more slowly -- and "your bill grew slowly overnight" is not a better
-- outcome than "the demo said come back tomorrow".
--
-- The IP is stored **hashed**, never in the clear. Rate limiting needs to know
-- that two requests came from the same place; it does not need to know where
-- that is, and a table of addresses paired with the questions people asked of a
-- religious text is a privacy liability nobody asked for. The hash is salted
-- with the deployment's master key so the table is not reversible by anyone who
-- obtains only the rows.
CREATE TABLE IF NOT EXISTS public_asks (
    ask_id      text PRIMARY KEY,
    ip_hash     text NOT NULL,
    question    text,
    -- Whether it reached the model. A refusal costs nothing and must not be
    -- counted against the daily cap, or a burst of rate-limited requests would
    -- close the demo for everybody.
    answered    boolean NOT NULL DEFAULT true,
    asked_at    timestamptz NOT NULL DEFAULT now()
);

-- The two queries this table exists for, and nothing else.
CREATE INDEX IF NOT EXISTS public_asks_ip ON public_asks (ip_hash, asked_at DESC);
CREATE INDEX IF NOT EXISTS public_asks_day
    ON public_asks (asked_at DESC) WHERE answered;
