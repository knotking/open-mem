# Changelog

What this repo became, for someone who was not here when it happened. The git
log records every change; this records the ones that alter what you can do, what
you must configure, or what was wrong before.

Newest first. Entries under `## Unreleased` have not been tagged.

---

## Unreleased

### Added
- **`POST /api/v1/ask` — reading the corpus by asking it.** Retrieval, then a
  model reading only the passages retrieval returned. Every factual sentence
  carries the bracketed number of the passage it came from, and the response
  ships those passages, so a claim can be checked rather than believed. It is
  deliberately not a second retrieval path: it calls `retrieve()` verbatim, so
  the ACL predicate, the corpus counts and the audit rows are the same code that
  serves search.
- Answers report `grounded`. When the passages do not support an answer the API
  says so and cites nothing, rather than composing a fluent paragraph from
  general knowledge. A model that claims grounding without citing anything is
  not believed — `grounded` requires a resolved citation.
- Retrieved records are fenced as evidence and the prompt says so explicitly. A
  stored record containing "ignore all previous instructions" is reported as
  content, never obeyed — anyone who can write to the corpus would otherwise be
  writing to the prompt.
- **Ask** panel in the sandbox: the answer, the evidence behind each citation,
  how many passages were read against how much of the corpus was enriched, which
  model build answered, and whether the text was stored.
- **Eight webhook providers beyond the generic one** — Slack, GitHub, Stripe,
  Linear, Shopify, Twilio, Microsoft Graph and Zoom. Each signs a different
  string over a different encoding, so the adapter carries the scheme, the
  handshake, the retry id and the field mapping as data rather than as branches.
- Provider presets in the inbound console, so configuring an endpoint is picking
  a name rather than reconstructing a signature scheme by hand.
- **Crawlers — the ingestion path for data that never announces itself.** A
  backfill and a poll are two schedules of the same thing, so there is one
  worker with three discovery strategies: `http` (a templated REST request with
  declared pagination, which covers enumerate/query/search for most APIs),
  `feed` (RSS, Atom, sitemap) and `traverse` (bounded link following). The
  provider-specific strategies in the design — a Drive folder, a Salesforce
  object — need OAuth connections that do not exist yet.
- A crawler discovers and emits; it does not fetch and does not enrich. Items
  leave through `POST /api/v1/write` exactly as an external producer's would, so
  nothing downstream can tell a crawled record from a webhook-delivered one, and
  a managed crawler holds no powers an external one lacks.
- **A dry run is mandatory before a crawler can be enabled**, and editing scope
  or strategy invalidates it. It walks the same code a live run does and stops
  short of the write, so its counts cannot drift from what would actually
  happen. Enabling without one returns `409`.
- Configs are declarative. Expressions are JMESPath — no side effects, no I/O,
  no loops — so a tenant-supplied transform cannot hang a worker or reach the
  network. Invalid expressions are refused at save time with `422`, not at 3am
  inside a six-hour run.
- Endpoints: CRUD on `/api/v1/crawlers`, `dry-run`, `run`, `runs`, and
  `/api/v1/crawl-runs/{run_id}` for detail and pause/resume/cancel. Cancelling
  keeps the checkpoint, so cancelling a long job is not an irreversible choice.
- `python -m memdog crawl-tick` runs one scheduler pass and exits, for Cloud
  Scheduler. It takes a Postgres advisory lock, so running it on several
  instances is safe rather than merely unlikely to overlap.
- A **Crawlers** panel in the sandbox with three presets, the dry-run gate
  enforced in the UI, and what the run found before anything is enabled.
- **Metrics and traces on the inbound and crawl paths**, which previously had
  none — the inbound path had no spans at all. New counters: `ingest.dropped`,
  `inbound.deliveries`, `inbound.rejected`, `crawl.discovered`, `crawl.emitted`,
  `crawl.dedupe_hits`, `crawl.runs`, `crawl.robots_denied`; histograms
  `crawl.duration` and `crawl.duration_vs_interval`. They cost nothing until an
  OTLP endpoint is configured.
- `crawl.discovered` is the one to alert on: a crawler that finds nothing fails
  at nothing, so an error rate stays flat while the data goes stale. Trending to
  zero against its own baseline is the crawler equivalent of a dead connection.
- `ingest.dropped` is a separate counter rather than a label on a failure
  metric, because a disabled webhook answers `200` and drops the payload — the
  error rate is correctly zero while data goes nowhere.
- Signature failures are counted even though they are raised before a delivery
  row exists, so they reach no other metric. A spike in them is the clearest
  sign of a rotated secret or someone probing an endpoint.
- Crawler run duration is recorded **against its schedule interval**, not alone.
  Above 1.0 the next tick always lands on a live run and the crawler overlaps
  forever — invisible in the duration by itself, since whether a run is too slow
  depends on the schedule.
- Crawler freshness (`seconds_since_last_success`) is on the list endpoint and
  shown in the console as a fresh/stale chip. It measures the last **success**,
  not the last attempt: a crawler failing every tick has a recent run and stale
  data, and nothing else tells those apart.
- **Telemetry now exports.** Traces go to Cloud Trace and metrics to Cloud
  Monitoring, via the Google exporters directly — those services do not speak
  OTLP, and this avoids running a collector purely to translate. Verified live:
  spans for `webhook.receive`, `crawl.run`, `crawl.discover`, `write`,
  `retrieve` and `enrich`, and per-crawler metric series.
- **A cardinality guard in `record()`.** A metrics store keeps one time series
  per distinct label combination, so an unbounded label multiplies the series
  count rather than adding a dimension — and that is how a metrics store falls
  over, taking the ability to see anything with it. `user_id`, `data_id`,
  `run_id`, `host`, `url` and friends are dropped from metrics and kept on
  spans. The measurement still goes out with its remaining labels: losing a
  dimension degrades a dashboard, losing the measurement hides the outage.
- **New env var**: `OTEL_GCP_PROJECT` selects the GCP exporters (already wired
  into `deploy/cloudrun.sh`). Unset, the service exports nothing, which stays
  the local default. The service account needs `roles/cloudtrace.agent` and
  `roles/monitoring.metricWriter`.
- **New dependencies**: `opentelemetry-exporter-gcp-trace`,
  `opentelemetry-exporter-gcp-monitoring`.
- `.claude/skills/changelog` and this file.

### Changed
- Subscription handshakes that carry nothing to verify are answered before
  authentication, declared per adapter rather than assumed. Microsoft Graph
  sends its validation request with an empty body and no `clientState`; without
  this the subscription could never be established. Slack and Zoom sign their
  challenge and are still verified first.
- The answer text is not stored by default. `answer_storage` defaults to
  metadata-only because an answer corpus is often more sensitive than the
  records it was built from; the query, the sources, the model and the latency
  are kept either way, so the query stays auditable without retaining content.
- A stored answer inherits the strictest access level among its sources, so it
  cannot become a way around the ACL on what it was built from.
- `query_sources.used` means *cited* for an answer, not merely *retrieved*.
  Passages the model saw and did not use are recorded as
  `retrieved_not_cited` rather than left claiming the answer rests on them.
- Crawled items are **not enriched unless the crawler asks**. A crawler is the
  one producer that can discover fifty thousand records unattended, and
  enriching them is a model call per chunk on data nobody has queried yet.
- The audit trail can now say a crawler acted. `actor_mode` admitted only
  `user` and `platform`, so crawled writes had to masquerade as one of them.
- Deleting a crawler keeps the data it wrote and disables its producer rather
  than removing it — the items still point at that producer for provenance.
- **New dependency**: `jmespath`. `pip install -e .` before deploying.

### Fixed
- A model rate limit surfaced as a `502` carrying the upstream provider URL. It
  is now a `429` with `Retry-After` — the same shape admission control already
  uses for a deep queue — and other upstream failures no longer echo the
  outbound request back to the caller.
- `/api/v1/runs/{run_id}` already existed for deletion and reprocess runs, so
  the crawler route registered at the same path was silently shadowed and every
  crawl-run lookup returned 404. Crawl runs now live at `/api/v1/crawl-runs/`.
- Two tables were minting the `run_` id prefix, so an id could no longer say
  which thing it identified. Crawl runs are `crun_`.
- A link crawl stored stylesheets as records: `text/css` passes a bare `text/`
  prefix check, so every page's stylesheet was fetched and kept, spending the
  crawl budget on assets. Asset URLs are now skipped before the fetch and the
  accepted content types are documents only.

### Migrations
- `0019_answers.sql` — adds `queries.answer_access_level` and extends the
  `query_sources.excluded_reason` enumeration. Run before deploying.
- `0020_crawlers.sql` — the six crawler tables, and extends the
  `audit_events.actor_mode` enumeration to admit `crawler`.

---

## 2026-08-28

### Added
- **The inbound path.** `POST /hooks/{producer_id}` is a public front door that
  normalises whatever a provider sends and calls the ordinary write API. It is a
  translator in front of the write path, not a second one.
- Per-producer signing secrets, HMAC verification with timestamp windows, and
  delivery records carrying the provider's own retry id so a redelivery is
  recognised as one.
- Provider rules can ignore an event outright — a bot echoing our own message
  back is accepted and dropped, not stored.
- **Deletion with a chosen blast radius**: one record, a whole memory of
  records, detachment from a memory only, or all account data. The console shows
  what each one would remove before it removes anything.
- Account deletion distinguishes personal data, which is erased, from data
  arriving through a shared connection or written at org visibility, which is
  retained with the reason stated. Revocation of keys, producers, connections
  and membership is immediate and unconditional either way.
- `verify_erasure()` re-queries every table that could hold a trace and reports
  whether the erasure is actually complete.

### Fixed
- The cascade left rows in `normalized_records` — a copy of personal data
  surviving its own deletion. Also `case_members` and `share_links`.

---

## 2026-08-27 — the implementation

The design became a running system: a FastAPI service on Cloud Run against a
private-IP Cloud SQL Postgres, with GCS for bytes.

### Added
- **The write path.** One endpoint, `POST /api/v1/write`, for every producer.
  Content is `Inline`, `Stored` or `Pending`; the write commits before it
  returns and the enrichment is queued behind it.
- **Recording and enrichment as two ordered events.** Adding data no longer
  implies paying for a model. `data.recorded` always; `enrichment.requested`
  only when asked, gated on the fetch that must precede it. Enrichment is off by
  default and can be overridden per write, including the prompt.
- **The domain event log is the record of work**; the queue only delivers. A
  `caused_by` gate keeps ordering without the queue having to guarantee it, and
  `graph.build.requested` is emitted with no consumer so the knowledge-graph
  seam exists before the worker does.
- **The reconciler.** Rows, not the queue, say what is outstanding — so a Cloud
  Run scale-in during enrichment is repaired rather than lost.
- **The readiness staircase** — `stored → searchable → enriched` — surfaced on
  every read, so "I uploaded it and search cannot find it" has an answer that is
  not a bug report.
- **Retrieval as one query with one plan**, vector and lexical arms fused, with
  the ACL predicate inside the query rather than filtering after it. The trace
  reports what was excluded and why: below the cut, or not yet searchable.
- **All data types.** 59 formats parsed; audio, video and images uploaded to
  GCS, interpreted, and playable in the console. Media was originally scoped out
  of v1; that decision was overridden deliberately.
- **Memories** — creation, membership, retyping with a preview, expiry policy on
  delete, and a default memory per user that nothing can be orphaned out of.
- **Cases**, sharing, uploads, settings with scope precedence and locks, the
  control plane, per-purpose model assignment, editable prompts, and audit.
- **The sandbox console** — sign-in through Firebase, a landing page, dark and
  light themes, collapsible navigation grouped by concern, and the retrieval
  trace as the output rather than an answer.
- OpenTelemetry spans propagated across queue hops, kept distinct from audit:
  one is for debugging, the other is evidence.
- Every artifact records the model, the served model build, and a
  `generator_version` fingerprinting the prompt, model, schema, parser and
  chunker together — so editing a default makes everything it produced
  detectably stale.

### Fixed
- `/healthz` returned 404 behind the Google Front End, which intercepts that
  path. Health moved to `/api/v1/health`.
- The console's API proxy failed *open*, falling back to the service key and
  silently promoting a signed-in user to org owner. It now fails closed with a
  401. The root cause was a web API key scoped to identitytoolkit only, which
  blocked token refresh.
- Ownership followed the producer rather than the writer, so a second user could
  not see their own writes.
- The reconciler could not recover a lost parse job: its query required
  `indexable_text`, which excludes precisely the items still needing parsing.
- The reconciler would have parsed items nobody asked to enrich.
- Events stranded in `dispatched` were never retried.
- `record_version` dropped revisions under concurrency.
- A `UNIQUE` constraint never fired for null scope ids.
- Binary content that happened to decode as UTF-8 was sniffed as text and
  crashed on a NUL byte.
- A generic MIME type beat a more specific file extension, so calendars and CSVs
  were indexed as raw text.
- The model fabricated a transcript for a 440 Hz test tone.

---

## 2026-08-26 to 2026-08-27 — the design

Eighty-four commits of specification before the first line of implementation.
The decisions that survived into the code:

- **One write path.** Four — an envelope endpoint, an item endpoint, a batch
  endpoint and an internal queue publish — meant four sets of admission control
  and an external crawler that could ingest records but not files. Collapsed
  behind registered producers.
- **Readiness is three states, not one.**
- **Deletion is asynchronous**, with a tombstone separating visibility from
  reclamation, and cleanup distinguished from erasure.
- **Memories are formed, not just routed** — membership is mutable, effective
  TTL is computed rather than stored, and type is changeable.
- **Audit is two stores**, and covers reads as well as writes.
- **Access is connection-scoped**, capabilities are key-scoped, and admin is a
  dual role that must be visible as one.
- **Model assignment is per (purpose, data type)**, with embeddings excluded
  because changing an embedding model invalidates a corpus.
- **Prompts are versioned defaults**, overridable at two levels.
- The schema, the blob layout, the telemetry catalogue, billing as three
  asynchronous stages, twelve open decisions in `TBD.md`, and a use-case catalog
  of twelve published cases.
