-- Where a model runs, so sensitivity can be enforced rather than recorded.
--
-- `data_type_profiles.sensitivity` has existed since the catalog shipped and
-- nothing read it: assignment validation selected the column beside `requires`
-- and used only `requires`. So `clinical_note` was marked `regulated`, and
-- assigning it to a cloud model succeeded. The comment on that column already
-- said a clinical or legal type "must not be routed to an unapproved provider";
-- this is the column that makes the check possible.
--
-- **`provider` cannot answer it.** Provider is who made the model; hosting is
-- where the bytes go. Ollama is the clearest case -- the same adapter speaks to
-- a process on this machine and to Ollama Cloud, and one of those receives the
-- content while the other does not. A residency rule keyed on the vendor's name
-- would call both of them safe.
--
-- **The default is `remote`, which is the failing-closed direction.** A card
-- registered by an operator who did not think about hosting describes a model
-- that might send content to a third party, and the assumption that costs
-- something is far better than the assumption that leaks something. The shipped
-- local models are corrected below by name.

ALTER TABLE model_cards ADD COLUMN hosting text NOT NULL DEFAULT 'remote'
    CHECK (hosting IN ('local', 'remote'));

COMMENT ON COLUMN model_cards.hosting IS
    'local: inference happens inside the deployment boundary and content does '
    'not leave it. remote: a third party receives the content. Defaults to '
    'remote so an undeclared card is treated as the riskier case.';

-- The two engines that genuinely run in-process. Named individually rather than
-- derived from `provider = 'local'`, because the point of this column is that
-- the provider name is not the authority on where something runs.
UPDATE model_cards SET hosting = 'local'
    WHERE model_id IN ('local-hash-v1', 'local-heuristic-v1');
