-- Settings, with the chain that governs.
--
--   per-request option -> user -> project -> org -> platform default
--                        most specific wins
--
-- Two qualifications turn that from a suggestion into a control:
--
--   * **Admin locks beat specificity.** Without them org policy is advisory,
--     and a compliance control a project can switch off is not a control.
--   * **A per-request option can never widen access or skip a phase.** Options
--     tune cost and latency. If a caller could set `redact: false` the
--     redaction rule would be decoration -- so that is enforced in code, not
--     here.

CREATE TABLE settings (
    setting_id  text PRIMARY KEY,
    scope       text NOT NULL CHECK (scope IN ('platform', 'org', 'project', 'user')),
    -- null at platform scope; otherwise the org, project or user it belongs to.
    scope_id    text,
    key         text NOT NULL,
    value       jsonb NOT NULL,
    -- Set at org scope to make a setting non-overridable below it.
    locked      boolean NOT NULL DEFAULT false,
    set_by      text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope, scope_id, key)
);

CREATE INDEX settings_lookup ON settings (key, scope);
