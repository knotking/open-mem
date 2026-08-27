# Feature Matrix

Every feature in the platform, with the phase that ships it and whether it is in MVP.

**Legend** — `●` in MVP · `◐` partial in MVP, column and enforcement only · `○` post-MVP ·
`△` proposed, not decided

---

## Ingestion

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| Unified write endpoint | `POST /api/v1/write` — one verb for every producer, `items[]`, `207` always | 1 | ● |
| Producer registry | `whk_` / `crw_` / `key_` / `upl_` — nothing writes anonymously | 1 | ● |
| `ContentRef` contract | `Inline` / `Stored` / `Pending` — enrichment cannot tell how content arrived | 1 | ◐ `Inline` only |
| Idempotency | Request-level key; fetch-level dedupe on `(provider, resource_id, etag)` | 1 / 4 | ◐ |
| Admission control | Producer, size, quota, queue depth, token budget — before anything is written | 1 | ● |
| Readiness staircase | `stored → searchable → enriched` on the response and on reads | 1 | ● |
| W1 enrich worker | Classify, route, agent, viewpoint, embed, entities | 1 | ● |
| W2 fetch worker | Resolve a reference, materialise bytes through the credential proxy | 2 | ○ |
| W3 crawl worker | Scheduled and manual discovery; backfill and polling collapsed into it | 5 | ○ |
| W4 scheduled worker | Subscription renewal, date-facet sweeps | 5 | ○ |
| W6 stream worker | Persistent socket sources | 5 | ○ |
| W7 reprocess worker | Config or schema change re-runs derived work | 2 | ◐ |
| W8 mutate worker | Upstream revision → new version, never in place | 2 | ○ |
| W9 retract worker | Delete and erasure cascade | 1 | ◐ single item |
| **W10 standing query** | Match new writes against saved selectors, deliver to a target | — | △ |
| Source adapters | `resolve` / `materialise` / `export` / `read` per source family | 3 | ○ |
| Connector catalog | 300+ documented, ~900 reachable through the credential broker | 3 | ○ |
| Direct upload | Text, file, URL, camera, voice, video | 4 | ○ |
| Bulk operations | Dry-run, per-item results, resumable runs | 4 | ○ |
| Format handling | 60+ types; PDF, Docs via `export`, media gated on cost | 4 | ◐ text only |
| Fan-out | One reference expands to N independently retryable jobs | 4 | ○ |
| Per-provider rate limits | Token buckets keyed `(provider, user)`; backfill deprioritised | 5 | ○ |

## Data model

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| **Memories** | Type = name + TTL + expiry policy; type mutable, not in the id | 1 | ● |
| Many-to-many membership | Mutable after write, individually and by selector | 1 | ● |
| `memory_key` | Natural key unique per `(project, type)`; writes upsert | 1 | ● |
| Effective expiry | Max across memberships, **computed never stored**, with its reason | 1 | ● |
| `orphan_delete` | Expiry never deletes something another memory holds | 1 | ● |
| `memory_links` | `part_of` / `derived_from` / `about` / `continues` / `supersedes` | 1 | ◐ |
| Dynamic membership | A bounded selector, re-evaluated | 2 | ○ |
| Memory compression | Summarise members, archive originals, record the source list | 2 | ○ |
| **Cases** | Declared subjects — patient, matter, deal, asset | 7 | ○ |
| Identifier correlation | Join on MRN, docket, serial — never entity resolution | 7 | ○ |
| Asserted vs inferred | Declared is authoritative; derived never drives access or lifecycle | 7 | ○ |
| Normalization | Canonical types, projections tagged with schema version | 2 | ◐ one schema |
| `identifiers[]` · `event_time` | Required fields; `event_time` distinct from ingestion time | 1 | ● |
| Versioning | Every mutation a new version, with diffs | 2 | ◐ column |
| Raw is truth | The original is never discarded by normalization | 1 | ● |

## Retrieval

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| Vector index | pgvector, inside the record store | 1 | ● |
| Lexical index | Postgres `tsvector` BM25 | 1 | ● |
| Hybrid retrieval | Combined, with reranking | 1 | ● |
| **ACL filtering inside the query** | Not post-filtering — the reason indexes live in the record store | 1 | ● |
| **Retrieval trace** | Ranked chunks with scores, and exclusions **with the reason** | 1 | ● |
| Composable primitives | Not five fixed modes — Family C requires composition | 2 | ○ |
| Facet search | Typed fields — amounts, dates, sentiment, device, units | 2 | ○ |
| Temporal graph | `valid_at` / `invalid_at`, what was true when | 7 | ○ |
| Claim index | Contradiction detection, evidence assembly | 7 | ○ |
| Intent index | Decisions and commitments as structured fields | 7 | ○ |
| Summary hierarchy | "Catch me up on Acme" | 7 | ○ |
| Conflict surfacing | A doc and a later thread disagreeing | 7 | ○ |

## AI and models

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| **MVP inference** | **Gemini Flash + Gemini embeddings**, plus **Ollama Cloud** as a second generation engine | 1 | ● |
| **Embeddings on one engine** | Not negotiable — mixed vectors corrupt an index with no error | 1 | ● |
| Per-provider credentials | Encrypted in the catalog, failing closed — two providers makes the path real | 1 | ● |
| **Pinned model versions** | Never a rolling alias — a floating `-latest` makes `generator_version` a lie | 1 | ● |
| Model catalog | Engine registration, encrypted, failing closed — the seam, filled once in MVP | 1 | ◐ |
| Assignment per purpose | Different models for embed, enrich, chat | 1 | ◐ |
| Local inference (Ollama) | Returns air-gap and $0 — a **base-URL change** against the adapter already shipped for Ollama Cloud | 6 | ○ |
| `model_id` per artifact | Without it, affected rows cannot even be identified | 1 | ● |
| **`generator_version`** | Hash of prompt, model, schema, parser, chunker — plus handler digest | 1 | ● |
| `served_by_model` | What actually answered, against what was intended | 1 | ● |
| Capacity × capability routing | Fallback chain by tier | 2 | ○ |
| **Embeddings never fall through** | Two embedding models produce incomparable vector spaces | 1 | ● |
| **Allow-list per project, fail closed** | The chain must not cross a legal or cost boundary | 2 | ◐ enforcement |
| Typed enrichment agents | Output schema is the contract, not the prompt | 2 | ◐ one agent |
| Staleness impact preview | What changing this model or prompt invalidates | 6 | ○ |

## Security and privacy

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| Tenancy scoping | `org_id` / `project_id` populated, **every query scoped** | 1 | ● |
| Connection-scoped ACL | Personal Gmail in a team org stays private, whatever the project default | 1 | ● |
| `shared_with` principals | user / group / project / org / public | 1 | ◐ |
| RBAC | Org roles, one enforcement path | 1 | ◐ |
| Auth seam | `TokenVerifier` — Firebase hosted, local password air-gapped | 1 | ◐ API key |
| **Capability-scoped keys** | A key can only do what was checked at creation | 1 | ● |
| Ephemeral token exchange | Key → short-lived JWT → gateway validates against our JWKS | 3 | ○ |
| Credential proxy | Workers receive references, never secrets | 2 | ◐ |
| Encryption failing closed | Never store a provider key in plaintext with a warning | 1 | ● |
| **Audit on every read** | Impossible to retrofit — March cannot be reconstructed | 1 | ● |
| Deletion cascade | Chunks, embeddings, blobs, entity contributions, summaries | 1 | ◐ single item |
| Provenance as a source **list** | What makes erasure through compression possible | 1 | ● |
| Legal hold | Suppresses expiry; refuses erasure **with a reason** | 7 | ○ |
| DSAR tooling · export | Data subject requests end to end | 8 | ○ |
| Admin sees metadata only | Structurally — no content renderer exists in that component set | 8 | ◐ rule |

## Operations

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| **Absence telemetry** | `producer.seconds_since_last_item` — the failure mode is silence | 1 | ● |
| `embed.distinct_models_per_index` | Catches the incomparable-vector-space corruption | 1 | ● |
| ingest → searchable · → enriched | The two SLIs component metrics cannot show | 1 | ● |
| Token accounting | Per user, model and agent; estimate against actual | 1 | ● |
| **Budgets at user and project scope** | One person's experiment must not spend the team's month | 1 | ● |
| Queue abstraction | NATS and Pub/Sub differ in acks, ordering, redelivery | 1 | ● |
| Dashboards · alerting | The full surface | 8 | ○ |
| Deployment variants | Local · GKE · Cloud, as three fillings of one role table | 6 | ○ |
| Unit and e2e tests per slice | Each slice ends in a test, not a demo | all | ● |

## Customization — post-MVP by decision

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| **Fixed write phase order** | ACL sealed before any hook — a privilege escalation boundary | 1 | ● |
| Read-only security envelope | Hooks cannot write ACL-determining fields | 1 | ● |
| `handler_digest` in the fingerprint | A column — adding a hash field later invalidates every fingerprint | 1 | ● |
| Normalization schema editor | Canonical types and field mappings, per scope | 6 | ○ |
| Prompt and schema overrides | Test-before-save, staleness preview, admin locks | 2 | ○ |
| Redaction rules | Pre-storage, declarative, never a model on the hot path | 6 | △ |
| Validation policy | `accept_raw` default; `reject` blocked for webhook producers | 6 | △ |
| **Custom worker code** | Dedicated clusters only; never the shared write path | 6 | △ |
| Custom fetch adapters | The internal system no connector reaches | 6 | △ |
| `derive` over `compress` | Study guides, flashcards, obligations — one generator registry | 6 | △ |

## Console

| Feature | What it does | Phase | MVP |
|---------|--------------|:-----:|:---:|
| **Sandbox** | Upload, staircase, search, trace — Phase 1's acceptance test with a face on it | 1 | ● |
| Item inspector | Six tabs, typed renderer registry with a raw fallback | 1 | ◐ raw + one |
| **Self-service loop** | Settings → upload → sandbox → query → cost, no admin involved | 1 | ● |
| Per-user telemetry | Their producers, their spend, their queue position — never absolute | 1 | ● |
| Query cost display | The cheapest possible governance on read-side spend | 1 | ● |
| Absence dashboard | Near-empty when nothing is wrong | 2 | ○ |
| Chat over retrieved context | Deliberately after the trace — if retrieval is wrong, chat cannot be right | 2 | ○ |
| Enrichment inspector | Viewpoint, entities, classification layer reached | 2 | ○ |
| Connect flows | OAuth — genuinely blocking, no API-only path exists | 3 | ○ |
| Crawler dry-run preview | A guardrail whose value is visual | 5 | ○ |
| Config A/B comparison | Is the expensive model worth it *on my data* | 6 | ○ |
| Case timeline | A JSON timeline is not a timeline | 7 | ○ |
| Deletion preview | One component, six operations | 8 | ○ |
| Admin console | Metadata only, by component-set construction | 8 | ○ |

---

## Reading the matrix

**MVP is narrow on surface and complete on structure.** Almost everything marked `●` is either the
spine — write, index, retrieve — or a **column and enforcement point** that cannot be added later:
tenancy, ACL, audit, `model_id`, `generator_version`, provenance lists, the write phase order.

That is the Phase 1 rule holding: *ship the column and the enforcement point; the surface follows.*
A UI can be built any time. A column you did not write cannot be truthfully backfilled, and an
enforcement point retrofitted into every query path touches everything.

**MVP inference is two engines: Gemini and Ollama Cloud.** All versions pinned, and **embeddings
stay on Gemini alone** — a second engine that can embed is exactly the condition under which
incomparable vectors enter one index by accident.

Two engines is deliberate rather than incidental. A catalog seam filled with one entry is
untested; a second entry proves `model_id`, `served_by_model`, per-engine credentials and the
allow-list all work before anything depends on them. And because Ollama Cloud speaks the same
protocol as local Ollama, **the riskiest deferred capability — air-gapped operation — gets
de-risked by a choice made for other reasons.**

The stated cost stands: **air-gap and $0 local inference are not true in MVP**, and both are
load-bearing in the positioning. But the path back is now a base URL, not an integration.

**The `△` rows are decisions, not backlog.** W10 standing queries, redaction, validation policy,
custom worker code and `derive` are designed but not scoped. Each has a recommendation in
[roadmap.md](roadmap.md#still-open).
