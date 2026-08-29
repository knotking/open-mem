-- Credentials that are exchanged rather than presented.
--
-- Every auth style so far sends a stored secret straight to the source. The
-- providers that matter most for connectors do not work that way: Microsoft
-- Graph, Salesforce and Zoom trade a client id and secret for a token that
-- lives an hour, and Google signs an assertion with a service-account key to do
-- the same.
--
-- This was described here for weeks as "needs OAuth", which was wrong in a way
-- worth naming. Interactive OAuth -- a browser, a redirect, a consent screen --
-- is how a *person* connects their own account. An organization connecting its
-- own data uses a grant with no human in it at all. The second is a POST, and
-- it is what a connector actually needs.
--
-- `auth_config` holds what the exchange needs and never the secret: the token
-- endpoint, the scopes, the tenant. The secret stays in `credential_ct`, where
-- it already was.

ALTER TABLE connections
    DROP CONSTRAINT IF EXISTS connections_auth_style_check;

ALTER TABLE connections
    ADD CONSTRAINT connections_auth_style_check CHECK (auth_style IN (
        -- Presented as stored.
        'bearer', 'header', 'query', 'basic',
        -- Exchanged for a short-lived token before every stale request.
        'client_credentials', 'google_service_account'
    ));

ALTER TABLE connections
    -- Never the secret. The token URL, the scopes, the tenant -- the parts an
    -- operator can read back without holding anything.
    ADD COLUMN auth_config jsonb NOT NULL DEFAULT '{}';

COMMENT ON COLUMN connections.auth_config IS
    'Non-secret parameters the token exchange needs. The credential itself '
    'stays in credential_ct.';
