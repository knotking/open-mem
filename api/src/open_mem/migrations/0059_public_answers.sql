-- A shared link is one question asked many times.
--
-- `/public/ask` spends a model call per request and charges it against a daily
-- cap shared by the whole gallery. That is the right shape for a visitor typing
-- their own question, and the wrong shape for a link: a demo link that does the
-- rounds is the *same* question from a few hundred people, which under the old
-- behaviour is a few hundred identical model calls and a cap exhausted by
-- lunchtime -- after which everyone who follows the link sees the allowance
-- message instead of the demo it was pointing at.
--
-- So an answer is kept and served again. The key is the corpus plus the
-- normalised question, because a link that has been through a chat client and
-- back differs from the original by whitespace and case and nothing else.
--
-- **Growth is bounded by the metering rather than by a sweeper.** A row is only
-- ever written on a cache *miss*, and a miss is a metered ask, so the table
-- cannot grow faster than the daily cap however many visitors arrive -- at most
-- `public_daily_cap` rows a day, and fewer in practice because the questions
-- worth sharing repeat. The TTL below then holds the total steady.
CREATE TABLE IF NOT EXISTS public_answers (
    demo_key     text        NOT NULL,
    question_key text        NOT NULL,
    question     text        NOT NULL,
    payload      jsonb       NOT NULL,
    answered_at  timestamptz NOT NULL DEFAULT now(),
    hits         integer     NOT NULL DEFAULT 0,
    PRIMARY KEY (demo_key, question_key)
);

-- For the age check on read and for evicting what has gone stale.
CREATE INDEX IF NOT EXISTS public_answers_age ON public_answers (answered_at);
