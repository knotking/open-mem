# Privacy Foundations

Most of [compliance.md](compliance.md) describes capabilities that can be added to a running
system: DSAR tooling, export, SSO, certification. This document is about the subset that **cannot**,
and therefore belongs in the first slice.

## The retrofit cost is not uniform

| Control | If deferred | Recoverable? |
|---------|------------|--------------|
| **Access audit** | Past access is unknowable — no record exists | **No. Ever.** |
| **Derived-artifact provenance** | You cannot determine what a summary was built from once the sources have changed | **Effectively no** |
| **Encryption at rest** | Full re-encrypt migration; key management designed under pressure | Expensive |
| **Per-item ACL** | No defensible default for existing rows — every choice is a guess | Guesswork |
| **Content classification** | Re-scan the entire corpus | Expensive |
| **Query-time ACL filtering** | Every retrieval path rewritten | Mechanical but wide |
| DSAR tooling, export, SSO, certification | Built later against existing data | Yes — defer these |

The first row is the argument. **"Who accessed this patient record in March?" has no answer if you
were not recording in March.** No migration recovers it, no amount of later engineering helps, and
it is exactly the question that gets asked after an incident.

## What belongs in the first slice

### 1. Access audit, from the moment access control exists

The instant there is an ACL, there is a question about who got past it. Audit starts with the first
read, not with the compliance push.

- Every read of an access-controlled item produces an audit record: who, what, when, which
  credential, which producer or surface
- **Append-only**, with retention measured in years rather than the three days that tracing
  memories keep
- **Separate store** from logs and traces — different retention, different mutability guarantee,
  different threat model
- Written on the read path, so it cannot be skipped by a code path that forgot

This is the `domain_events` store from [telemetry](../operations/telemetry.md). It exists in
Phase 1 for this reason, not because events are useful for debugging.

### 2. Provenance on every derived artifact

The [delete cascade](compliance.md) is a Phase 8 capability, but it is only *possible* if every
derived artifact has recorded what it came from — from the first one.

| Artifact | Must record |
|----------|-------------|
| Embedding | `source_id`, `source_version` |
| Summary | The full `(source_id, version)` **list** — summaries span items |
| Extracted claim | Source and location |
| Entity / graph fact | Contributing sources |
| Case-level artifact | Member set at generation time |

A summary written in month one that spans forty items, without its source list, is unerasable in
month twelve. Not difficult — **unerasable**, because nothing records that the paragraph someone
wants deleted came from the document they are asking about.

The staleness fields already carry `source_id` and `source_version`. Making the multi-source case a
*list* rather than a single reference is the whole change, and it costs nothing now.

### 3. Encryption at rest, failing closed

Currently unspecified for user content — credentials are encrypted, content is not documented as
being so.

- Content and derived artifacts encrypted at rest
- **Fail closed.** The documented behaviour for provider keys — plaintext with a warning if
  encryption is unavailable — must not be repeated for content. A misconfiguration must refuse the
  write
- Envelope encryption from the start: a key-encryption key held externally, per-tenant data keys.
  Retrofitting per-tenant keys onto a single-key corpus is a full re-encrypt
- Key rotation possible by design. "Set once, never rotate" is an operational dead end

### 4. Per-item ACL and query-time filtering

Already in Phase 1 for tenancy reasons. Restating why it is also a privacy foundation: an item
written without an access level has no defensible default later, and post-rank filtering both
degrades results and leaks existence.

### 5. Content classification

A flag, set at ingest, marking content as regulated or containing personal data.

It gates three things already designed:

- **Classification-gated inference** — regulated content pins to local models and fails closed
  rather than falling through to a third party
- **Erasure scope** — knowing which items are in scope for a subject request
- **Export and residency** — what may leave which boundary

Start deterministic: source-based (an HR connector is PII by construction), pattern-based for
obvious identifiers, and caller-declared. Model-based detection can come later; **the field must
exist from the first write** or classification means re-scanning the corpus.

### 6. Log discipline

Zero cost, and irreversible if got wrong — a secret or a document body written to a log is in a
system with different retention and different access control, and it stays there.

**Never logged:** item content or excerpts · prompt and completion bodies · credentials, including
in URLs and error payloads · personal identifiers in free text.

Prompts *contain user content*. "Log the prompt for debugging" is a data-exfiltration path that
looks like observability. Where genuinely needed, it goes behind a time-boxed, audited, per-tenant
flag — not a log level.

---

## What can safely wait

Deferring these is a scheduling decision, not a design failure:

DSAR tooling and subject-indexed inventory · export and portability UX · SSO, SAML, SCIM ·
break-glass and ethical walls · legal hold · per-org retention policy · data residency ·
certification · the delete cascade *implementation* — its **hooks** are above.

---

## The two hazards, restated as build order

Both are documented in [compliance.md](compliance.md). Their placement matters here:

| Hazard | When it must be closed |
|--------|----------------------|
| **Fallback chain transmits regulated content to a third party** | **Phase 1**, with the first inference call. It is a policy check before the chain, and it is cheap — but every call made before it exists is an untracked disclosure |
| **Compression defeats erasure** | Hooks in Phase 1–2 (provenance above); the cascade itself later. The hazard is created the moment the first summary is written without its source list |

---

## Requirements

- **FR-PRIV-1** Every read of an access-controlled item MUST produce an append-only audit record
  identifying accessor, item, time, credential and surface.
- **FR-PRIV-2** Audit records MUST be stored separately from logs and traces, with independent
  retention, and MUST NOT be mutable by the application.
- **FR-PRIV-3** Every derived artifact MUST record its complete source set, as a list where more
  than one source contributed.
- **FR-PRIV-4** User content and derived artifacts MUST be encrypted at rest, and encryption MUST
  fail closed.
- **FR-PRIV-5** Encryption MUST use envelope encryption with per-tenant data keys, and key rotation
  MUST be possible without re-authenticating tenants.
- **FR-PRIV-6** Every item MUST carry a classification flag indicating regulated or personal
  content, set at ingest.
- **FR-PRIV-7** Classification MUST gate the inference fallback chain: regulated content pins to
  local inference and fails closed.
- **FR-PRIV-8** Item content, prompt bodies, completion bodies and credentials MUST NOT be written
  to logs.
- **FR-PRIV-9** Access control MUST be applied within the retrieval query, never as a post-ranking
  filter.
- **FR-PRIV-10** An item MUST NOT be written without an access level.
