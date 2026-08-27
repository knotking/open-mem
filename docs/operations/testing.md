# Testing

## The invariants are the test suite

This system's characteristic failure is silence. A revoked connection returns nothing. An embedding
lands in the wrong vector space and ranks anyway. A stale fact answers confidently and wrongly.
Production monitoring catches these *eventually*; tests are where they get caught **before** a
corpus is contaminated.

So the highest-value tests are not coverage of functions. They are executable versions of the
"MUST NOT" statements in the requirements.

| Invariant | Test |
|-----------|------|
| Enrichment cannot observe provenance | Feed the same logical item as `Inline` and as `Stored`; assert **byte-identical** enrichment output |
| The enrich worker holds no integration credentials | Assert its environment and message envelope contain no secret; attempt an upstream call from an agent and assert it cannot authenticate |
| Embeddings never fall back | Make the primary embedder unavailable; assert `embed_status = pending`, and assert **no row was written with a different `model_id`** |
| One vector space per index | Property test: after any sequence of writes and provider failures, `distinct(model_id) == 1` |
| ACL filters in the query, not post-rank | Request top-10 where 7 are inaccessible; assert **10 accessible results**, not 3 |
| Derived artifacts inherit the strictest source | Summarise mixed-ACL items; assert the summary carries the intersection |
| Watermark advances only on success | Kill a crawl at 60%; assert the watermark did not move and the next run re-covers |
| A disabled producer accepts and drops | Assert `2xx`, no item created, and `ingest.dropped` incremented |
| The write is synchronous and durable | Kill the process immediately after `2xx`; assert the item survives |
| `is_downloaded` is underivable from the outside | Assert the field cannot be set through any public path |
| Legal hold blocks erasure | Erase across a held case; assert partial completion naming what was withheld |

Each of those corresponds to a defect that would otherwise ship, look fine, and surface months
later.

---

## Shape of the suite

| Layer | Scope | Speed | Runs |
|-------|-------|-------|------|
| **Unit** | Pure logic — classification cascade, `ContentRef` derivation, JMESPath mapping, ACL composition, staleness fingerprint | ms | every commit |
| **Contract** | One component against a mocked boundary — write API, worker consumers, adapter verbs | fast | every commit |
| **Integration** | Real record store, real queue, mocked model and upstreams | seconds | every commit |
| **End-to-end** | The full slice, in-process queue, tiny local model or recorded fixtures | minutes | every PR |
| **Adversarial** | Cross-tenant, ACL, credential boundary | seconds | every commit — **never optional** |
| **Failure injection** | Provider outages, worker death, budget exhaustion | minutes | nightly + pre-release |
| **Evaluation** | Retrieval and enrichment *quality* against a golden set | minutes | nightly, **not a CI gate** |
| **Soak** | Capacity at target scale | hours | pre-release |

Integration tests use `testcontainers` for the record store and queue; HTTP boundaries are mocked
with `respx`. Nothing in the fast tiers reaches a real model or a real provider.

---

## Testing what is not deterministic

The pipeline's core calls a model. That does not make it untestable — it changes what you assert.

**Assert shape, never content.** An agent's output is tested against its declared schema, required
fields, and invariants ("every citation index resolves to a retrieved chunk"). Never against an
expected sentence.

**Make the model layer injectable.** Every test tier below evaluation runs against a fake model
client. This is a design requirement, not a testing convenience — and it is a further argument for
the LLM-proxy shape, since a proxy is trivially replaceable and an embedded client is not.

| Fake | Used for |
|------|----------|
| **Deterministic stub** | Returns fixed structured output — unit and contract tests |
| **Recorded fixtures** | Real responses captured once, replayed — integration and e2e |
| **Failure stub** | Times out, returns malformed JSON, exhausts the chain — failure injection |
| **Counting stub** | Asserts *how many* calls were made — catches the batch-endpoint amplification, and verifies the ~80%-no-LLM claim |

That last one is worth calling out: `enrich.classification_layer` is a production metric, but the
claim that most items never reach an LLM should also be a **test** — ingest a representative corpus
against a counting stub and assert the LLM was called for fewer than N of them. Otherwise the
deterministic cascade silently degrades and only the cost line notices.

**Quality is evaluated, not asserted.** Retrieval and summarisation quality belong in a golden-set
evaluation that runs nightly and reports drift. Making it a CI gate produces flaky builds and
teaches people to re-run until green.

---

## Testing time

Several behaviours are time-dependent, and every one of them is a flaky test waiting to happen if
the clock is real.

**Inject the clock everywhere.** Any test that sleeps is a test that will be deleted.

| Behaviour | Test |
|-----------|------|
| Memory TTL and expiry | Advance the clock; assert cleanup |
| `valid_at` / `invalid_at` | Point-in-time query returns the fact true *then*, not now |
| **`event_time` vs ingestion time** | Backfill records with old event times; assert the timeline orders by event time and the recently-ingested 2019 record sorts first |
| Watermarks | Overlapping windows do not duplicate; gaps do not skip |
| Subscription renewal | Advance past expiry; assert renewal fired |
| Idempotency window | Replay inside and outside the window; assert both behave correctly |

The `event_time` one deserves its own test rather than being folded into timeline tests. It is the
bug that renders perfectly.

---

## Adversarial tests, which are not optional

Cross-tenant leakage is the highest-severity defect class here, and it produces no error.

```
tenant A writes → tenant B retrieves → assert zero results
```

Run that against **every** retrieval path, not just the main one: vector, lexical, hybrid, graph,
case-scoped, similar-case, MCP tools, RAG chat, and the citation payload. A leak through the
citation list is still a leak.

Also adversarial:

- A `personal`-scoped connection's data must not surface to an org admin, **including through the
  proxy**
- Derived artifacts — claims, concepts, summaries, graph facts — must not surface content whose
  source is inaccessible
- An MCP-scoped key must be **structurally unable** to reach control-plane endpoints; assert `403`
  for each one, not just for a sample
- Ethical walls survive a membership change

---

## Failure injection

| Injected | Expected |
|----------|----------|
| Connection revoked mid-fetch | Terminal, `needs_reauth`, **no retry storm** |
| Provider returns `429` | Backoff, bucket narrows, run continues — not a failure |
| Embedder unavailable | Defer, never substitute |
| Model chain fully exhausted | Retry with backoff; artifact not written half-formed |
| Worker killed mid-crawl | Resume from checkpoint; no duplicates, no skips |
| Worker killed mid-fetch | Job redelivered; no partial object in the store |
| Budget exhausted mid-run | Stops `partial`, watermark unmoved |
| Queue unavailable | Writes still succeed; items land unenriched |
| Record store read-only | Writes fail cleanly with a structured error, not a 500 |

The queue-unavailable case is the one that proves the central claim — that ingest latency is a
database write and a dead pipeline delays rather than loses.

---

## Test data

Real customer data cannot be used, and synthetic text is not enough for the format tiers.

- **A fixture corpus** of real-shaped files: HEIC, `mbox` with thousands of messages, `amr` voice
  note, scanned vs digital PDF, password-protected PDF, macro-enabled Office, a 500k-row
  spreadsheet, mixed encodings, a zip with a deep tree
- **A golden retrieval set** — queries with known-correct answers, for evaluation
- **Recorded provider responses** for each tier-1 connector, refreshed deliberately
- **A tenancy fixture** — at least three orgs with overlapping entity names, so cross-tenant
  entity merging is caught

That last one matters: two orgs both holding "Acme Corp" must never merge, and the only way to know
is to have both in the fixture.

---

## What cannot be unit tested

| Behaviour | Covered by |
|-----------|-----------|
| **Air-gap operation** | CI job with network egress blocked; the full local flow must pass |
| **Stream worker sharding** | Multi-replica integration test asserting one connection per account and no duplicate ingestion |
| **Advisory-lock leader election** | Multi-process test: exactly one scheduler ticks |
| **Connection-pool exhaustion** | Load test at max replica count against the real ceiling |
| **Cardinality limits** | Metric-name audit in CI against the allowed-label list |
| **Retrieval quality** | Golden-set evaluation, nightly |
| **Capacity at scale** | Soak, pre-release |

The air-gap test is the one that keeps a headline claim honest. It is easy to write code that
"works offline" and quietly depends on one DNS lookup.

---

## Per-phase gates

Each slice's exit criterion is a test, not a demo.

| Phase | Gate |
|-------|------|
| **1 · Spine** | Write → search → cite, **scoped to a project with an ACL**. Cross-tenant adversarial suite green. `distinct(model_id) == 1` property holds. Kill-after-`2xx` durability test passes |
| **2 · Depth** | Provenance contract test (`Inline` ≡ `Stored`). Counting-stub test asserting the deterministic cascade handles most of a representative corpus. Reprocess rebuilds exactly the artifacts a generator change marked stale |
| **3 · Connectors** | Credential-boundary test. Revoked-connection failure injection. A tier-1 connector ingests, enriches and is findable end to end |
| **4 · Uploads/bulk** | MIME-spoof test — a renamed executable must not route by its extension. Batch partial-failure returns `207` with per-item results. Archive-bomb caps hold |
| **5 · Crawlers** | Interrupt-and-resume with no duplicates or skips. Watermark-on-success-only. Dry-run writes nothing and spends nothing. Robots compliance |
| **6 · Variants** | **Air-gap CI job passes.** The same contract suite passes against all three queue implementations |
| **7 · Team & cases** | Ethical-wall and break-glass tests. Timeline orders by `event_time` under backfill. Cross-tenant entity non-merging |
| **8 · Scale** | Legal-hold-blocks-erasure. Delete cascade verified across every derived store. Soak at target |

## Requirements

- **FR-TEST-1** Every documented invariant MUST have a corresponding automated test.
- **FR-TEST-2** The model layer MUST be injectable so that all test tiers below evaluation run
  without contacting a provider.
- **FR-TEST-3** Time-dependent behaviour MUST be tested against an injected clock; tests MUST NOT
  sleep.
- **FR-TEST-4** Cross-tenant isolation MUST be tested against **every** retrieval path, including
  citation payloads and MCP tools.
- **FR-TEST-5** Quality evaluation MUST run against a golden set and MUST NOT gate CI.
- **FR-TEST-6** Air-gap operation MUST be verified by a CI job with egress blocked.
- **FR-TEST-7** Each phase's exit criterion MUST be expressed as a passing test rather than a
  demonstration.
