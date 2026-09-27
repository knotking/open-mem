-- Credentials for crawlers, so an authenticated source is configuration.
--
-- The templated `http` strategy already covers enumerate, query and search for
-- most REST APIs -- that is what makes breadth cheap. It could reach only
-- *public* ones, because a crawler had nowhere to keep a secret: `crawlers.py`
-- never mentioned a credential, and the only place to put a token was the
-- `config` jsonb, in the clear, next to the `connections` table that exists
-- precisely to hold one enveloped.
--
-- That is the whole gap between "three public feeds" and "any API with a
-- token", and it is one column.
--
-- **`ON DELETE RESTRICT`, not CASCADE.** Deleting a connection out from under a
-- running crawler would leave it enabled, scheduled, and quietly failing every
-- tick with an authentication error -- the shape of failure this codebase keeps
-- finding, where nothing errors loudly and the data simply stops arriving.
-- Refusing the delete makes the operator disable the crawler first, which is
-- the decision they were making anyway.

ALTER TABLE crawlers
    ADD COLUMN connection_id text REFERENCES connections ON DELETE RESTRICT;

COMMENT ON COLUMN crawlers.connection_id IS
    'The credential this crawler authenticates with. NULL is a public source '
    'and is not a degraded case -- sitemaps and RSS need nobody''s permission.';

CREATE INDEX ON crawlers (connection_id) WHERE connection_id IS NOT NULL;

-- How a credential is presented. APIs differ here far more than they differ in
-- pagination, and the difference is small and closed enough to be data: a
-- bearer token, a named header, a query parameter, or basic auth. An open
-- "template the header yourself" field would put the secret back in the config
-- where this migration is taking it out of.
ALTER TABLE connections
    ADD COLUMN auth_style text NOT NULL DEFAULT 'bearer'
        CHECK (auth_style IN ('bearer', 'header', 'query', 'basic')),
    -- The header or parameter name, for the two styles that need one.
    ADD COLUMN auth_name text;
