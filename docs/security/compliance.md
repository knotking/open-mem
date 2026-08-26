# Privacy & Compliance

Engineering analysis, not legal advice — but the architectural consequences are concrete and
several are load-bearing.

## The deployment variant decides the regulatory posture

**Self-hosting is not merely a privacy feature — it changes who the regulated party is.**

| | Local / self-hosted | Cloud (hosted by us) |
|---|---|---|
| Our role under GDPR | **Neither controller nor processor** — we never touch the data | **Processor** — DPA required |
| HIPAA | Customer is the covered entity; **no BAA needed from us** | We are a Business Associate — **BAA required** |
| Sub-processors | None, if inference is local | Cloud provider, inference, auth, credential broker |
| Cross-border transfer | None | Requires a transfer mechanism for EU data |
| Breach notification | Customer's obligation | Ours, on a clock |
| Certification burden | Effectively none | SOC 2, and audit evidence |

This is the strongest commercial argument for the local variant — and the opposite of how it
currently reads in the roadmap, where self-hosting is treated as the hobbyist tier while being the
only configuration that sidesteps the entire compliance apparatus.

## Two hazards in the current design

### Hazard 1 — the fallback chain crosses a legal boundary invisibly

The documented chain is *local model → cloud model → third-party API*. When the local model is
unavailable, regulated content is **automatically transmitted to a third party**, with no error,
no prompt and no record distinguishing which items took which path.

Under HIPAA that is a disclosure of PHI to a party that may have no BAA. Under GDPR it is an
undisclosed transfer. Architecturally it is the same defect as the embedding-fallback bug — an
automatic substitution that is safe for availability and unsafe for correctness — except the
consequence is legal.

**Fix:** data classification gates the chain. Items marked regulated pin to local inference and
**fail closed** rather than falling back. Every inference call records which provider served it.

### Hazard 2 — compression defeats erasure

Memory compression summarises N items into prose. On an erasure request the source item is
deleted — but its content **survives inside the summary**, and inside any claim, concept key or
graph fact extracted from it.

A "right to be forgotten" implementation that deletes the row and leaves the substance in derived
text has not erased anything. This is why the delete cascade cannot be deferred: it becomes
exponentially more expensive once a production corpus has compressed mixed-subject content.

## GDPR obligations against current state

| Obligation | State | Gap |
|------------|-------|-----|
| **Erasure** (Art 17) | missing | W9 cascade — must reach embeddings, entities, graph facts, summaries, blobs |
| **Access / DSAR** (Art 15) | missing | Subject-indexed inventory across all stores |
| **Portability** (Art 20) | partial | Workspace export exists as a manifest; needs machine-readable completeness |
| **Storage limitation** (Art 5) | **strong** | Typed memories with per-type TTL map onto this unusually well |
| **Data minimisation** (Art 5) | tension | A product that ingests everything from 900 sources is in structural tension with minimisation — needs explicit per-connection scoping |
| **Records of processing** (Art 30) | partial | Entity-to-source mapping gives provenance; needs a processing register |
| **Privacy by design** (Art 25) | **strong** | Private-by-default ACLs, connection-scoped inheritance, local inference |
| **Breach notification** | missing | Needs a real audit trail to even determine scope |

**Embeddings, extracted entities and graph facts are derived from personal data and should be
treated as personal data.** An erasure that removes the source row but leaves its vector and
entity node has not completed.

## HIPAA — and the agent nobody costed

**The pipeline ships a Medical / DICOM agent.** That is an explicit design decision to ingest and
analyse protected health information. It places HIPAA squarely in scope, and nothing in the
current design addresses it: no BAA path, no audit controls, no minimum-necessary enforcement, no
documented encryption of PHI at rest, and an inference fallback chain that can transmit PHI to a
third party.

In the hosted variant, HIPAA requires a BAA with *every* sub-processor touching PHI, including
inference providers. **The current cloud inference stack is unlikely to be BAA-able.** Either
healthcare is a self-hosted-only story, or the cloud inference choice has to change.

| Safeguard | State |
|-----------|-------|
| Access control — unique user ID, minimum necessary | partial — per-item ACLs exist; the global unscoped key defeats them |
| **Audit controls** | **missing** |
| Integrity — detect improper alteration | good — versioning with diffs |
| Transmission security | partial — TLS at edges; the fallback chain is the hole |
| Encryption at rest | **unspecified** — credentials are encrypted; user content is not documented as encrypted |

## Tracing memories are not an audit log

Observability is persisted as `tracing` memories with a **3-day TTL**, and it is sampled. An audit
trail must be durable, complete and tamper-evident, and must record *who accessed which record
when* — a different dataset with different retention and a different threat model. Three-day
sampled traces cannot answer a breach-scope question about last quarter.

## "Public" needs defining

The `public` access level currently means "any authenticated user in the organization" — which is
*internal*, not public. Three distinct things share the word:

- **Org-visible** — the current meaning; harmless
- **Externally shared** — a link outside the tenant. Not supported, and the feature most likely to
  cause accidental disclosure once added
- **Publicly-sourced** — [crawled](../ingestion/crawlers.md) web content, which carries different
  licensing and copyright exposure and must be tagged at ingest so retrieval can distinguish it

## What to build, in order

1. **Classification-gated inference** — mark regulated content, pin to local models, fail closed,
   record the serving provider *(hazard 1)*
2. **Delete cascade with derived-artifact reach** — designed now even if built later *(hazard 2)*
3. **Immutable access audit log**, separate from tracing, with real retention
4. **Encryption at rest for user content**, failing closed
5. **Retire the global unscoped key** — it defeats minimum-necessary by construction
6. **Subject-indexed inventory** for DSAR
7. SOC 2, SSO/SAML, SCIM — the entry ticket for team and enterprise

Items 1 and 2 get materially harder with time. Everything else can be added to a running system;
those two get baked into data.
