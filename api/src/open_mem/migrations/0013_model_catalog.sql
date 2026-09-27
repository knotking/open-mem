-- The model catalog, and the data the mapping is derived from.
--
-- Assignment is per `(purpose, data_type, scope)` rather than one model for
-- everything, because the purposes have genuinely different cost-per-quality
-- curves. Classification is not reasoning and should not be sized like it;
-- transcription is not extraction. This project already has evidence for that:
-- a general model asked to transcribe a tone invented a conversation, while a
-- purpose-built transcriber correctly returned nothing.

CREATE TABLE model_cards (
    model_id     text PRIMARY KEY,
    provider     text NOT NULL,
    family       text,
    -- What it can do, which is what an assignment is checked against.
    capabilities text[] NOT NULL DEFAULT '{}',
    context_tokens int,
    dimensions   int,
    -- Per claim: some of this is measured, some is what the vendor says. The
    -- distinction matters when a mapping is derived from it.
    declared_by  text NOT NULL DEFAULT 'vendor'
                 CHECK (declared_by IN ('vendor', 'measured', 'operator')),
    verified_at  timestamptz,
    licence      text,
    hardware     jsonb NOT NULL DEFAULT '{}',
    pricing      jsonb NOT NULL DEFAULT '{}',
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- What a data type *requires*, so an assignment can be checked rather than
-- merely accepted.
CREATE TABLE data_type_profiles (
    data_type       text PRIMARY KEY,
    requires        text[] NOT NULL DEFAULT '{}',
    context_floor   int,
    -- A clinical or legal type must not be routed to an unapproved provider,
    -- and that is a property of the type, not of the request.
    sensitivity     text NOT NULL DEFAULT 'standard'
                    CHECK (sensitivity IN ('standard', 'restricted', 'regulated')),
    volume          text
);
