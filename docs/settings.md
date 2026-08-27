# Settings

Thirteen documents state a precedence rule. This is the one that governs, and the register of what
is actually settable.

---

## One chain, everywhere

```
per-request option  →  user  →  project  →  org  →  platform default
                    most specific wins
```

Two qualifications make it a control rather than a suggestion:

- **Admin locks beat specificity.** An org that locks a setting makes it non-overridable below.
  Without locks, org-level policy is advisory — and a compliance control a project can switch off
  is not a control.
- **A per-request option can never widen access or skip a phase.** `options` tunes cost and latency.
  If a caller could set `redact: false`, the redaction rule would be decoration.

## Not every setting belongs at every level

The interesting design work is not the chain — it is deciding where each setting is *allowed* to
live. Three categories, and the boundaries are deliberate.

### Settings a user owns, and an admin cannot set for them

| Setting | Why it stops at the user |
|---------|--------------------------|
| **[Activity capture](activity-memory.md)** | An admin enabling it for a member is surveillance wearing a recall feature's interface |
| Personal connection scope | Which of *their* accounts they connect, and as what |
| Their own default memory | It is theirs |
| Personal notification preferences | — |

Where org policy genuinely requires one of these, it must appear to the user as **a locked setting
they can see**, never as one silently enabled.

### Settings that must never be user-level

| Setting | Why |
|---------|-----|
| **ACL defaults and connection scope semantics** | A user who can widen their own defaults can widen them past org policy |
| **Model allow-list** | The [fail-closed boundary](operations/technology.md). A user routing PHI to a cloud model is a disclosure, not a preference |
| **Redaction rules** | A control the subject can disable is not a control |
| Validation policy | Data-quality guarantees are an organisational contract |
| Retention floors | Erasure obligations are not personal preferences |
| Audit capture | See [audit](security/audit.md) — mandatory, no exemptions |

### Settings that are project-scoped by nature

Model assignments, memory types, normalization schemas, prompts, crawler configs, budgets. These
describe a *corpus*, and a corpus belongs to a project.

---

## The register

`U` user · `P` project · `O` org · `PL` platform · **bold** = the level that owns it by default ·
🔒 = commonly locked · ⚡ = [versioning event](#settings-that-are-versioning-events)

### Data handling

| Setting | Levels | Default | Notes |
|---------|--------|---------|-------|
| Memory types — name, TTL, `on_expiry` | **P** O PL 🔒 | 4 shipped types | [memories.md](memories.md) |
| Memory routing rules | **P** O | thread-key routing | Must be bounded |
| Default memory scope | — | per (project, user) | `shared`-scope items go to the project default |
| Normalization schema | **P** O PL 🔒 ⚡ | mem-dog canonical types | [normalization.md](ingestion/normalization.md) |
| Field mappings | **P** O ⚡ | per provider | |
| Validation policy | **P** O 🔒 | `accept_raw` | `reject` refused for webhook producers |
| Redaction rules | **P** O 🔒 ⚡ | none | Post-MVP. Narrowing one is irreversible |
| Retention / TTL floors | **P** O 🔒 | none | Legal hold overrides |

### Enrichment and models

| Setting | Levels | Default | Notes |
|---------|--------|---------|-------|
| `agent_config` — prompt + schema + model, per `data_type` | **P** O PL 🔒 ⚡ | shipped per type | One object, [not three settings](operations/model-catalog.md) |
| Model assignment per `(purpose, data_type)` | **P** O 🔒 ⚡ | `*` rows shipped | Embeddings accept `*` only |
| **Model allow-list** | **O** P 🔒 | all registered engines | Never user-level. Fails closed |
| Engine registration + credentials | **O** 🔒 | none | Encrypted, failing closed |
| Embedding model + dimension | **P** 🔒 ⚡ | `gemini-embedding-001` | Changing dimension **upward** is a full re-embed |
| Chunk size | **P** ⚡ | ≤ embedding input limit | **Validated against the model card**, refused if over |
| `embed` / `enrich` per producer | **P** | producer-declared | Bulk defaults to `enrich: false` |

### Access and privacy

| Setting | Levels | Default | Notes |
|---------|--------|---------|-------|
| Connection scope | **U** (own) / **O** (semantics) | declared at connect | Decides the ACL of everything it produces |
| Project ACL default | **P** O 🔒 | `private` | |
| Team-upload default ACL | **P** O 🔒 | *open decision* | |
| Sharing — external / public | **O** 🔒 | disabled | |
| Answer retention | **P** O 🔒 | `metadata` | `none` where the question is sensitive |
| **Activity capture** | **U** | **off** | An admin may not enable it for a member |
| Audit | — | **always on** | Not a setting |

### Identity and cost

| Setting | Levels | Default | Notes |
|---------|--------|---------|-------|
| Key capability defaults | **O** 🔒 | **nothing checked** | See below |
| Key max lifetime | **O** 🔒 | 90 days | |
| **`registration_mode`** | **O** PL 🔒 | **`invite_only`** | `open` and `disabled` also available |
| Invite expiry | **O** 🔒 | 7 days | Single-use, revocable, audited on create *and* redeem |
| Invite email binding | **O** 🔒 | **on** | An unscoped link is transferable by design |
| Auth providers | **PL** O | per variant | |
| Budget | **U** **P** O | none | Both levels — one person's sandbox must not spend the team's month |
| Rate limits | **O** PL | per provider | |

---

## Defaults are policy, not convenience

The most consequential line in this document is not a setting. It is that **every default is a
decision made on someone's behalf**, and the ones that matter are all defaults-off:

| Default | If it were the other way |
|---------|-------------------------|
| Key capabilities: **nothing checked** | The over-scoped key becomes the path of least resistance, and an MCP key deletes an org |
| Activity capture: **off** | A recall feature ships as surveillance |
| Answer retention: **metadata** | A second corpus accumulates, more sensitive than the first |
| Sharing externally: **disabled** | Public exposure is one click from a user who did not know what `public` meant |
| Registration: **invite-only** | Anyone who signed up before you closed it is already inside |
| Bulk `enrich`: **false** | A 50k import silently spends a month's budget |
| `orphan_delete`, never unconditional | A container nobody thought owned anything deletes data |

Each of these is one boolean. Each flipped the other way is a documented failure mode.

## Settings that are versioning events

Marked ⚡ above. These feed [`generator_version`](operations/schema.md), so changing one has three
obligations rather than one:

1. **Test against a sample first** — the tenant's own data of that type, before it can be saved
2. **Preview the impact** — *"this affects 4,102 items of 380,000"*, computed against real data
3. **Enqueue W7 reprocess**, or record that the corpus is **knowingly** mixed

> A configuration change looks retroactive and is not. Edit a field mapping and the corpus does not
> re-map; yesterday's records keep yesterday's shape, and a query spanning the change returns two
> shapes with no marker between them.

Per-type keying is what makes this affordable — changing `enrich · code` invalidates code artifacts,
not the corpus.

## Lock semantics

A lock is set at org (or platform) level and makes a setting non-overridable below.

- Locking is **audited**, with before and after
- A locked setting is **visible** to those it constrains, with the level that set it — a control
  people cannot see is one they route around
- Locking does **not** retroactively change existing data; it constrains future configuration.
  Where existing config violates a new lock, the UI must list the violations rather than silently
  resetting them

## API

Every setting is readable and writable through `/api/v1/settings`, scoped, with the effective value
and its **source level** both returned:

```json
{ "key": "agent_config.medical_record.model",
  "effective": "medgemma",
  "source": "org",
  "locked": true,
  "overridable_at": [] }
```

**Returning `source` and `locked` alongside the value is what makes the chain debuggable.** *"Why is
this model being used?"* has an answer in one call, instead of requiring someone to check four
levels by hand and guess.

---

## Requirements

- **FR-SET-1** All settings MUST resolve through one precedence chain: request → user → project →
  org → platform, most specific wins.
- **FR-SET-2** Admin locks MUST override specificity.
- **FR-SET-3** A per-request option MUST NOT widen access, skip a pipeline phase, or bypass
  redaction.
- **FR-SET-4** Activity capture MUST be settable only by the user it concerns.
- **FR-SET-5** ACL defaults, the model allow-list, redaction rules, validation policy and retention
  floors MUST NOT be user-level.
- **FR-SET-6** A locked setting MUST be visible to those it constrains, with the level that set it.
- **FR-SET-7** Lock changes MUST be audited with before and after state.
- **FR-SET-8** Settings that feed `generator_version` MUST require a sample test, MUST show an
  impact preview, and MUST enqueue reprocess or record the corpus as knowingly mixed.
- **FR-SET-9** Reads MUST return the effective value, its source level, and whether it is locked.
- **FR-SET-10** Chunk size MUST be validated against the assigned embedding model's declared input
  limit and refused if it exceeds it.
- **FR-SET-11** Key creation MUST default to no capabilities granted.
