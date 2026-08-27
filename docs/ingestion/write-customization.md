# Customizing the Write Path

Yes — the write path is customizable, at five points. This document collects them, because they
are currently specified in five different places and **a single write touches four of them at
once**. Nobody had written down the order they apply in, or what happens when two disagree.

Writing that order down turned out to matter more than adding features to it.

---

## What is already customizable

| Surface | Scope | Owning doc |
|---------|-------|-----------|
| **Normalization schema** — canonical types and their shape | project → org → global | [normalization.md](normalization.md) |
| **Field mapping** — `source_path → target_field`, transforms, `on_missing` | per (provider, target_type) | [normalization.md](normalization.md) |
| **Extraction prompts and output schemas** — what the agents produce | project → org → shipped, with admin locks | [workers.md](workers.md#extraction-prompts--standard-or-overridden) |
| **Memory types and routing** — name, TTL, expiry policy, which memory an item lands in | per deployment | [../memories.md](../memories.md) |
| **Producer defaults** — ACL scope, `embed`/`enrich` policy, target memory type | per producer | [write-api.md](write-api.md#producers) |
| **Per-request options** — `enrich`, `priority` | per call | [write-api.md](write-api.md#the-endpoint) |

Every one of these follows the same precedence rule — **most specific wins, project → org →
shipped** — which is the tenancy model applied consistently. That much was already right.

---

## The write pipeline has a fixed order, and that is the point

A write is nine phases. **Four are customizable. Five are sealed.** Which is which is not a
convenience decision — it is the security boundary.

```
  ┌─────────────────────────────────────────────────────────────┐
  │  1  Authenticate · resolve producer            SEALED       │
  │  2  Admission control                          limits only  │
  │  3  ACL assignment — from connection scope     SEALED  ★    │
  ├─────────────────────────────────────────────────────────────┤
  │  4  Validation                                 CUSTOM       │
  │  5  Transform · redact                         CUSTOM  (new)│
  │  6  Normalization — schema + field mapping     CUSTOM       │
  ├─────────────────────────────────────────────────────────────┤
  │  7  Persist raw + projection                   SEALED       │
  │  8  Memory · case routing                      CUSTOM       │
  │  9  Enqueue enrichment                         SEALED       │
  └─────────────────────────────────────────────────────────────┘
                                    ★ everything below 3 runs
                                      inside an ACL already fixed
```

### The finding — ordering is a privilege escalation boundary

An item's ACL derives from the **connection scope** and, in places, from its metadata. A
customization hook that can edit metadata is therefore a hook that can edit visibility — unless
the ACL is already sealed by the time it runs.

Put phase 3 after phase 5 and a project-authored transform rule can move a `private` item into
`org` visibility by rewriting a tag. Nothing errors. Nothing looks wrong in the audit log, because
the item was *written* with the visibility it ended up with.

So the rule is absolute:

> **ACL is assigned before any customizable phase runs, and no customizable phase may write to an
> ACL-determining field.** Hooks receive content and metadata inside an envelope whose security
> fields are read-only.

This costs nothing to honour now and is close to unfixable later, because by then projects will
have written rules that depend on running earlier.

### Why customization is declarative, not customer code

Every hook here is a **declarative rule the platform evaluates**. None of them is customer code
executed in the write path.

That is a tenancy decision, not a taste one. The write path is shared: one project's arbitrary
code in it becomes every project's latency, every project's crash, and — given the phase-3 rule
above — every project's security boundary. There is no version of in-process customer code that is
safe here.

**Customers who genuinely need arbitrary logic already have the answer:** run it before the write
and post the result through a `key_` producer. That is exactly what the external-ETL producer class
is for, and it puts the code in their process where it belongs.

---

## Gap 1 — redaction has nowhere to run

The strongest customization request the design cannot serve today is *"never store this."*

A project handling clinical or financial data wants an SSN, a card number or a patient name
stripped **before** anything is persisted. Today the only tools are downstream: classify it after
storage, then run an erasure cascade. That works, and it is strictly worse — the data existed, it
was backed up, and the cascade has to reach every derived artifact.

**Not storing is categorically better than storing and deleting.** Phase 5 is where that runs.

```
RedactionRule           scope: project | org
  match                 declarative pattern — regex, named detector, field path
  action                drop_item | redact_span | hash | tokenize
  applies_to            content | metadata | both
  record                always — see below
```

### Redaction collides with an existing invariant, and the collision resolves cleanly

[normalization.md](normalization.md#two-invariants) states: **raw is truth; never discard the
original.** Redaction discards the original on purpose. These look contradictory.

They are not, once you notice the two are lossy for different reasons:

| | Normalization | Redaction |
|---|---|---|
| Lossy | **by accident** — mappings have bugs, schemas evolve | **by intent** — the original must not exist |
| So keep raw? | **Yes** — it is the recovery path | **No** — keeping it defeats the entire purpose |

The invariant is therefore sharpened rather than broken: **raw is truth as admitted.** Redaction
happens at the boundary, before anything becomes truth. What is never admitted was never raw.

### But a redaction must still be auditable

The content is gone; the *fact* must not be. Otherwise "why does this record have a hole in it?"
and "is our redaction rule even firing?" are both unanswerable — and a rule that silently stopped
matching looks exactly like a corpus that stopped containing SSNs.

```
redaction_events
  data_id, rule_id, rule_version
  action, span_count
  at
```

This is the [characteristic failure mode](../operations/telemetry.md) again: the system fails by
**silence**. A redaction rule that matches nothing produces the same output as a rule that is
working perfectly, so the count is the only signal, and it has to be recorded.

### Redaction must not call a model on the hot path

Same argument [normalization.md](normalization.md#llm-assisted-mapping) makes about mapping:
runtime LLM inference on the write path is nondeterministic, expensive, and latency the write
budget does not have. Identical inputs would redact differently across runs, which for a privacy
control is disqualifying.

Rules are declarative patterns. Build the **suggester** offline — point it at sample payloads, get
proposed rules, review and store them declaratively. Detection quality improves by improving the
rule set, not by asking a model twice.

---

## Gap 2 — the rejection policy is fixed, and one class of customer needs the opposite

Today, a record that fails normalization lands raw with `normalization_status = failed` and stays
retryable. **This is the right default** — losing data because a mapping had a bug is the worse
failure, and it is the failure that is unrecoverable.

But a regulated project may need the opposite: *reject non-conforming data at the door rather than
store it*. Storing it, flagged, still means storing it — and for some corpora that is the
compliance breach, not a step towards fixing one.

```
validation_policy       scope: project
  on_validation_failure     accept_raw (default) | reject
  required_fields           beyond identifiers[] and event_time
```

### The tension worth stating rather than hiding

`reject` pushes the failure onto the producer — and **most producers cannot handle it.** A webhook
gets a `4xx` and, depending on the provider, either retries forever or drops the event silently.
The data is then gone, which is the failure mode `accept_raw` exists to prevent.

So `reject` is only honest when the producer can act on rejection:

| Producer | `reject` viable? |
|----------|:----------------:|
| `key_` client / ETL — synchronous, sees the `207` | **Yes** |
| `upl_` upload — a person is watching | **Yes** |
| `whk_` webhook — provider-controlled retry | **No** — it will be lost |
| `crw_` crawler — ours, can re-queue | Yes, with a DLQ |

**Recommendation:** allow `reject` per project, but **refuse to enable it for webhook producers**,
and say why at configuration time rather than discovering it as data loss.

---

## Gap 3 — changing a customization is a versioning event

[workers.md](workers.md#an-override-is-a-versioning-event) already establishes this for prompt
overrides, and [normalization.md](normalization.md#two-invariants) tags projections with the schema
version that produced them. The rule needs to hold for **every** write-path customization, because
they share one property that surprises people:

> **A customization change looks retroactive and is not.** Edit a field mapping and the corpus
> does not re-map. Yesterday's records keep yesterday's shape, and a query spanning the change
> returns two shapes with no marker between them.

Exactly the [generator-version problem](../use-cases-catalog.md) in another form. Same resolution:

- Every write-path config is **versioned, never mutated in place**
- Every stored artifact records **which version produced it**
- Changing one **enqueues W7 reprocess** for affected data, or the corpus is knowingly mixed —
  and "knowingly" means it is stated, not discovered

Redaction is the exception that proves it: **W7 cannot un-redact.** Reprocessing under a *narrower*
rule cannot recover what a broader rule removed, because the source is gone. Widening a redaction
rule is safe and reprocessable; narrowing one is not, and the UI must say so before it applies —
the same preview-before-destructive-change treatment [re-typing a memory](../memories.md) gets.

---

## Precedence, in one place

All five surfaces resolve identically. Stating it once beats stating it five times and hoping they
stay consistent:

```
per-request option  →  producer default  →  project  →  org  →  shipped default
```

Two qualifications:

- **Admin locks beat specificity.** An org that locks a normalization schema or a redaction rule
  makes it non-overridable at project scope. Without locks, org-level policy is advisory, and a
  compliance control that a project can switch off is not a control.
- **A per-request option can never widen access or skip a phase.** `options` tunes cost and
  latency — `enrich`, `priority`. It does not select ACLs, skip validation, or bypass redaction.
  If a caller could set `redact: false`, the redaction rule would be decoration.

---

## API

```
CRUD  /api/v1/normalization-schemas          scoped, versioned
CRUD  /api/v1/field-mappings                 per (provider, target_type)
CRUD  /api/v1/redaction-rules                scoped, versioned, lockable
CRUD  /api/v1/validation-policies            per project
CRUD  /api/v1/agent-configs                  prompts and output schemas
POST  /api/v1/write-config:test              dry-run a payload through phases 4–6
```

The last one carries more weight than its size suggests. A customization you cannot test against a
real payload before enabling gets tested in production against live data, which for a redaction
rule means the test failure is a disclosure. [workers.md](workers.md#test-before-save) already
requires this for prompts; it applies to the whole customizable set.

---

## Requirements

- **FR-WC-1** The write pipeline MUST have a fixed, documented phase order, and the order MUST NOT
  vary by producer, project or configuration.
- **FR-WC-2** ACL assignment MUST complete before any customizable phase executes.
- **FR-WC-3** No customizable phase may write to an ACL-determining field. Security fields MUST be
  read-only to hooks.
- **FR-WC-4** Write-path customization MUST be declarative and evaluated by the platform.
  Customer-supplied code MUST NOT execute in the shared write path.
- **FR-WC-5** Redaction MUST be applied before persistence, never as a post-storage correction.
- **FR-WC-6** A redaction MUST record rule id, rule version, action and match count, even though
  the content is not retained.
- **FR-WC-7** Redaction MUST NOT invoke a model on the write path. Rules MUST be declarative;
  model assistance MUST be confined to offline rule suggestion.
- **FR-WC-8** Validation failure policy MUST be configurable per project, defaulting to
  `accept_raw`. `reject` MUST NOT be enablable for producers that cannot act on rejection.
- **FR-WC-9** Every write-path customization MUST be versioned and never mutated in place, and
  every artifact MUST record the version that produced it.
- **FR-WC-10** Changing a write-path customization MUST enqueue W7 reprocess for affected data, or
  MUST record that the corpus is knowingly mixed.
- **FR-WC-11** Narrowing a redaction rule MUST warn that prior removals are unrecoverable before
  the change is applied.
- **FR-WC-12** Admin locks MUST override specificity, so org-level policy cannot be disabled at
  project scope.
- **FR-WC-13** Per-request options MUST NOT widen access, skip a phase, or bypass redaction.
- **FR-WC-14** Every customization surface MUST be testable against a sample payload before it is
  enabled.
