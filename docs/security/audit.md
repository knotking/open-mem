# Audit

**Reads are audited comprehensively. Everything else was specified in single lines scattered across
five documents**, under two different names — `access_log` in the schema, `audit_events` in the
architecture. This resolves both: the two names exist because they are genuinely two stores, and
the coverage gap is real and closed here.

---

## Two stores, deliberately

| | `access_log` | `audit_events` |
|---|---|---|
| Records | **Reads** — every retrieval hit, every fetch | **Everything else** — writes, changes, decisions |
| Volume | Very high — one row per item returned | Low — one row per action |
| Partitioned | By month | By year |
| Retention | Policy-bound, typically 1–2 years | **Never purged** |
| Answers | *"Who accessed this in March?"* | *"Who made this public, and when?"* |

Mixing them is the mistake. A single log means the interesting events — a share created, a key
granted admin, a retention policy loosened — **drown in millions of read rows**, and one retention
policy has to serve both a high-volume operational record and a permanent governance record. They
are different data with different questions, so they are different tables.

## What is audited

### Data operations

| Event | Recorded |
|-------|----------|
| Read | Principal, key, item ids, query id, at |
| Write | Principal, producer, item ids, at |
| Mutation | Principal, item, version before → after, at |
| **Deletion** | Principal, scope, counts, `deleted_at` and `purged_at`, at — **and this record survives the deletion** |
| Export | Principal, scope, item count, destination |

### Access and sharing — the category most often missed

| Event | Why it matters |
|-------|----------------|
| **ACL changed on an item** | *"Why can they see this?"* has no answer otherwise |
| **Share created or revoked** | Including the principal shared with, and any expiry |
| **Made `public`** | The single most consequential one-click action in the product |
| Group membership changed | Grants access transitively, so it is an access change |
| Role changed | Same |
| Connection scope changed | Changes the ACL of everything that connection produces *afterwards* |

**This is the sharpest gap the survey found.** Reads were thoroughly specified and access *changes*
were not — yet *"who made this public?"* is the question asked after an incident, and like read
audit it cannot be reconstructed later. A share is a state, and a state records only its current
value; without an event, the moment it changed is gone.

### Configuration

Every write-path and enrichment configuration change: prompts, output schemas, model assignments,
memory types, normalization schemas, redaction rules, validation policy, budgets.

Each records **before and after**, because *"the extraction got worse last Tuesday"* is answerable
only by diffing what changed against when it changed. This is also what makes
`generator_version` legible in retrospect rather than merely correct.

### Identity and credentials

| Event | Recorded |
|-------|----------|
| Key created | Principal, **capability scope**, expiry |
| Key first used | Often the real signal — a key created months ago suddenly waking |
| Key revoked | Principal, reason |
| Authentication failure | Principal (or attempted), source, count |
| Break-glass access | Principal, justification, scope, duration |
| Admin viewing a tenant | What was viewed — metadata only |

### What is deliberately not audited

- **Content.** See the rule below.
- **Credential values**, ever — only that a credential was used.
- **Question text**, where the project's [answer-retention policy](../operations/schema.md) is
  `none`. If the question is the sensitive part, auditing it defeats the setting.

---

## Three rules that make the log trustworthy

### 1 · Audit records reference by id, never by content

An audit record reading *"user X opened document 'Acme layoff plan Q3'"* **contains content.**

That matters more here than in most systems, because the
[admin console is metadata-only by construction](../ui-design.md) — it is built from a component
set with no content renderer, so disclosure is structurally impossible. **An audit log that embeds
titles or excerpts is a back door straight through that boundary**, and it is a back door that
looks like diligence.

So audit rows carry `data_id`, `project_id`, `principal_id` — identifiers, resolvable only by
someone who already has read access to the thing itself.

### 2 · Reading the audit log is an audited event

Otherwise the audit log is the one place a person can look without leaving a trace, which is
precisely the place someone looking for something would go.

The rule terminates rather than recursing: reading `audit_events` writes to `audit_events`, and a
read of *that* is the same event class, so the chain does not grow without bound.

### 3 · Append-only is a permission, not a guarantee

`INSERT` and `SELECT` only is the right grant, and it stops the application from rewriting history.
It does not stop anyone with database access.

For deployments where the log is evidence rather than a convenience, **hash-chain the records** —
each row carries the hash of its predecessor, so any modification or excision breaks the chain
verifiably. This is what "immutable audit log" in
[Phase 8](../roadmap.md) should mean concretely, and it costs one column and one index.

Until then, the honest claim is *append-only*, not *immutable* — and the two should not be used
interchangeably in anything a customer reads.

---

## The record

```
audit_events
  event_id       ULID — ordered by time, by construction
  at             explicit, because the ULID is convenience and this is evidence
  org_id · project_id
  principal_id   who — a user, a service identity, or the platform
  key_id         which credential, where one was used
  action         data.read | data.write | acl.change | share.create | config.update | …
  target_type · target_id
  before · after jsonb — for changes; null for actions
  reason         free text, required for break-glass and admin access
  source_ip · user_agent
  prev_hash      the chain, where enabled
```

`action` is a **closed vocabulary**, not free text. An audit log you cannot filter reliably is an
audit log nobody queries, and a typo'd action name is a row that disappears from every search that
would have found it.

## Where the surface is

| Surface | Sees |
|---------|------|
| **A user** | Their own activity, and access to their own data |
| **An org admin** | All activity in the org — metadata only |
| **A platform admin** | Cross-org operational events, never tenant content |
| **Export** | Signed, for a regulator or an incident review |

The user-facing view matters more than it first appears: *"who in my organisation has read my
data?"* is a question people are entitled to ask, and a system that can answer it for admins but
not for the person whose data it is has chosen a side.

---

## Requirements

- **FR-AUD-1** Reads MUST be recorded in `access_log`; all other audited actions MUST be recorded in
  `audit_events`. They MUST NOT share a table.
- **FR-AUD-2** ACL changes, share creation and revocation, group and role changes, and connection
  scope changes MUST be audited as events.
- **FR-AUD-3** Configuration changes MUST record before and after state.
- **FR-AUD-4** Key creation MUST record the capability scope granted; first use MUST be recorded.
- **FR-AUD-5** Audit records MUST reference by identifier and MUST NOT contain content.
- **FR-AUD-6** Reading the audit log MUST itself be audited.
- **FR-AUD-7** `audit_events` MUST be `INSERT`/`SELECT` only, MUST NOT be purged, and MUST survive
  deletion of the data it describes.
- **FR-AUD-8** `action` MUST be a closed vocabulary.
- **FR-AUD-9** Break-glass and admin access MUST require a stated reason, recorded.
- **FR-AUD-10** A user MUST be able to see who has accessed their own data.
- **FR-AUD-11** Where hash chaining is enabled, the log MUST be verifiable end to end. Without it,
  the log MUST be described as append-only rather than immutable.
