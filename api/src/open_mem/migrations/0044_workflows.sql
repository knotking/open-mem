-- Long-running state machine instances.
--
-- The engine lives outside open-mem: it calls in to record input and is told
-- when state changes. So this is the **system of record** -- state, history,
-- deadlines -- and not an executor. Every column below follows from that split.
--
-- A directed state graph that may contain cycles, deliberately, because
-- `review -> changes_requested -> review` is a legitimate workflow and a DAG
-- cannot express it. Three consequences the schema has to answer: there is no
-- topological order, so progress cannot be measured as depth; termination is
-- not guaranteed, so a budget is the only thing distinguishing a long loop from
-- an infinite one; and a state can be re-entered, so *the history* rather than
-- the current state is the thing worth storing well.
CREATE TABLE workflow_definitions (
    definition_id   text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    external_id     text NOT NULL,
    name            text NOT NULL,
    description     text,
    -- states{} and transitions[], validated by a Pydantic model the way a
    -- crawler's config is. Cycles pass validation; an unreachable state, an
    -- unknown target and a terminal state with an exit do not.
    config          jsonb NOT NULL,
    -- Bumped on every edit that changes behaviour. An instance pins the version
    -- it started on: a definition edited mid-flight must not silently move a
    -- thousand running instances onto a graph they never entered.
    config_version  int NOT NULL DEFAULT 1,
    -- The only thing separating a legitimate cycle from an infinite loop.
    max_transitions int NOT NULL DEFAULT 1000,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    UNIQUE (project_id, external_id)
);

CREATE TABLE workflow_instances (
    instance_id     text PRIMARY KEY,
    org_id          text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id      text NOT NULL REFERENCES projects ON DELETE CASCADE,
    definition_id   text NOT NULL REFERENCES workflow_definitions ON DELETE CASCADE,
    definition_version int NOT NULL,
    external_id     text NOT NULL,
    -- The subject this workflow is about, when there is one. Kept separate from
    -- the case rather than folded into it: one patient can carry three
    -- concurrent workflows, and closing a workflow does not close the patient.
    case_id         text REFERENCES cases ON DELETE SET NULL,
    -- A **cache**. The transition log is the record, and `/verify` re-folds it
    -- to prove this column rather than asking anyone to trust it.
    current_state   text NOT NULL,
    -- The optimistic concurrency token. An input names the sequence it believed
    -- it was acting on, and a conditional update is what makes two actors
    -- racing on one instance resolve as one winner and one 409 rather than two
    -- transitions from the same state.
    current_seq     bigint NOT NULL DEFAULT 0,
    -- When the current state runs out of time, if it has a TTL. Null is not
    -- "no deadline yet" -- it is a state that cannot time out.
    deadline_at     timestamptz,
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'errored', 'cancelled')),
    transition_count int NOT NULL DEFAULT 0,
    -- Its own ACL, like a case: a workflow over a restricted subject is not
    -- readable by everyone who can see the project.
    access_level    text NOT NULL DEFAULT 'private',
    shared_with     jsonb NOT NULL DEFAULT '[]',
    owner_id        text REFERENCES users ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,
    UNIQUE (project_id, definition_id, external_id)
);

CREATE INDEX workflow_instances_state ON workflow_instances (project_id, current_state);
-- The tick's only query, and it should stay one index lookup however many
-- instances are running.
CREATE INDEX workflow_instances_due ON workflow_instances (deadline_at)
    WHERE deadline_at IS NOT NULL AND status = 'running';
CREATE INDEX workflow_instances_case ON workflow_instances (case_id)
    WHERE case_id IS NOT NULL;

-- THE RECORD. Everything else here is derived from this table.
CREATE TABLE workflow_transitions (
    transition_id   text PRIMARY KEY,
    instance_id     text NOT NULL REFERENCES workflow_instances ON DELETE CASCADE,
    -- Per instance and gapless. A gap means a transition was lost, which is the
    -- one thing a history cannot survive quietly.
    seq             bigint NOT NULL,
    from_state      text NOT NULL,
    to_state        text NOT NULL,
    -- The input's name, or `@timeout` when a deadline moved it. Prefixed so a
    -- caller can never define an input that impersonates the clock.
    trigger         text NOT NULL,
    actor_user_id   text REFERENCES users ON DELETE SET NULL,
    actor_key_id    text,
    actor_kind      text NOT NULL CHECK (actor_kind IN ('human', 'key', 'system')),
    -- The input as memory, when one was recorded. NULLed rather than cascaded
    -- on erasure: **erasing an email must not erase the fact that the order was
    -- approved.**
    data_id         text REFERENCES data_items ON DELETE SET NULL,
    payload         jsonb NOT NULL DEFAULT '{}',
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (instance_id, seq)
);

CREATE INDEX workflow_transitions_history ON workflow_transitions (instance_id, seq DESC);
CREATE INDEX workflow_transitions_data ON workflow_transitions (data_id)
    WHERE data_id IS NOT NULL;

-- Mirrors `case_members`, so retrieval can scope to an instance the same way it
-- scopes to a case rather than growing a second mechanism.
CREATE TABLE workflow_instance_members (
    instance_id     text NOT NULL REFERENCES workflow_instances ON DELETE CASCADE,
    data_id         text NOT NULL REFERENCES data_items ON DELETE CASCADE,
    basis           text NOT NULL DEFAULT 'asserted'
                    CHECK (basis IN ('asserted', 'inferred')),
    added_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (instance_id, data_id)
);
