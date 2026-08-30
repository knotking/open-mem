-- What a run cost, which `llm` mode gave it something to say.
--
-- This column was briefly added by editing 0034 instead, and that is the bug
-- worth recording. **A migration is immutable once it has been applied.**
-- `schema_migrations` records the version, the runner skips anything already
-- there, and so an edit reaches only databases that have never seen the file.
--
-- It is invisible locally, which is the dangerous part: the suite drops the
-- schema and re-migrates on every run, so it always sees the edited version and
-- passes. Production had already applied 0034, never re-read it, and answered
-- every backtest with `column "model_calls" does not exist`.
--
-- `IF NOT EXISTS` so a database created from the edited 0034 during that window
-- converges here rather than failing.

ALTER TABLE alert_runs ADD COLUMN IF NOT EXISTS model_calls int NOT NULL DEFAULT 0;
