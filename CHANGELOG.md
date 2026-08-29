# Changelog

What this repo became, for someone who was not here when it happened. The git
log records every change; this records the ones that alter what you can do, what
you must configure, or what was wrong before.

Newest first. Entries under `## Unreleased` have not been tagged.

---

## Unreleased

### Added
- **Every model call is now metered, and what it cost is recorded rather than
  counted.** One `usage_events` row per inference call, carrying who pays, which
  engine actually answered, and input, output and cached tokens as three
  separate numbers. The telemetry counters that existed before are in-process
  and droppable under load, which is the right instrument for "is inference
  working" and the wrong one for "who spent this".
- **A call that failed is billed for what it generated.** A request that
  produced three thousand tokens and then timed out consumed three thousand
  tokens; counting only successes under-reports spend, and in the direction that
  produces a surprise bill. Breaker-skipped engines are recorded too, because a
  chain permanently serving from its fallback otherwise looks exactly like a
  chain with no primary.
- **`crossed_to_paid`** marks a fallback that moved a call from a free local
  engine to a paid cloud one. It is its own flag rather than something inferred
  from a non-zero fallback depth: money appearing where there was none is a
  category change, not a degradation, and the two want different alerts.
- **`GET /api/v1/usage`** — spend today against the ceiling that binds, and a
  breakdown by purpose, engine and status. A spending control nobody can see is
  a spending control nobody trusts.
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
- **Model routing is an ordered chain, not a single engine.** A provider that
  rate-limits or errors falls through to the next; the chain always ends at a
  local engine that needs no network, so an outage becomes a worse answer
  rather than no answer. This fixes an observed failure: chat returned `429`
  for an hour on free-tier quota while a working local answerer sat idle.
- Only **availability** failures fall through. A `429`, a `5xx` or a timeout
  means the model never answered. A schema or parse failure means it answered
  badly — a prompt problem a weaker model is unlikely to fix — so those stay
  terminal and reach the DLQ instead of quietly costing a second call.
- A circuit breaker stops hammering a dead engine, but is never applied to the
  last step in a chain: an open breaker on the only remaining engine would turn
  the protection into the outage.
- Answers and artifacts now carry `fallback_depth` and `served_by_engine`, and
  the console shows a chip when something other than the primary answered.
  Running permanently on a fallback is otherwise invisible — the answers keep
  arriving, just worse than the ones being paid for. The `generator_version`
  follows the engine that actually answered, so a fallback artifact is never
  attributed to the primary's fingerprint.
- New metrics `inference.fallback_depth` and `inference.attempts` (by engine and
  outcome: served, unavailable, rejected, skipped).
- **Manual scheduler tick**: `POST /api/v1/crawl-tick` and a **Run due crawlers**
  button. Scoped to the caller's organization — the advisory lock stops two
  passes at once but says nothing about whose crawlers a pass picks up.
- The chat panel is now called **Chat** rather than Ask, with example questions
  on the empty state. It was there before and hard to find.
- **A prompt per kind of thing.** The prompt register went from 12 entries to 24
  and the classifier from 8 MIME types to 39 (plus 83 extensions), so the
  formats the parsers already handled now reach a prompt written for them:
  spreadsheet, presentation, calendar, contact, log, config, audio, video,
  archive and geo.
- **Audio and video had no classification at all** — an mp3 was `binary_blob`
  and extracted with the generic prompt *after* being transcribed, which is
  exactly where a prompt most needs to say that speaker labels are unreliable
  and garbled names must not be normalised into plausible ones.
- A spreadsheet is no longer summarised as prose; the prompt asks for the
  table's shape — columns, row count, ranges — and forbids inventing totals.
- Two new prompts exist to prevent harm rather than improve quality: **config**
  must never reproduce a secret (the value would reach the summary, then the
  embedding, then an answer, where it cannot be recalled), and **contact** must
  not enrich, because a guessed employer is indistinguishable from an entered
  one afterwards.
- A coverage test now requires the classifier and prompt registers to agree, so
  a type with no prompt fails the build rather than silently degrading.
- **Retrieval is semantic.** The embedder moved from `local-hash-v1` — hashed
  term frequencies, not learned meaning — to `gemini-embedding-001@768`.
  Queries that share no vocabulary with their answers now work: "why did people
  not able to pay?" returns the checkout postmortem, where the lexical arm
  returns nothing at all.
- Documents and queries are embedded **asymmetrically** (`RETRIEVAL_DOCUMENT` vs
  `RETRIEVAL_QUERY`). A question and the passage answering it are different
  kinds of text, and symmetric embedding is much of why naive vector search
  disappoints.
- The embedder deliberately has **no fallback**, unlike every other engine.
  Vectors from two models in one index are not comparable, so degrading would
  silently corrupt retrieval for every row it touched — and a bad vector, unlike
  a bad summary, is invisible. Unavailability defers instead.
- **New env vars**: `EMBED_ENGINE=gemini`, `EMBED_MODEL=gemini-embedding-001`
  (both wired into `deploy/cloudrun.sh`). Switching engines invalidates existing
  vectors; the reconciler re-embeds them, and retrieval returns nothing for the
  affected rows until it has, rather than comparing across vector spaces.

- **Entities — graph layer 1.** Records now resolve the people, organizations
  and things they name into a typed entity layer, with `GET`/merge/undo
  endpoints and an **Entities** panel under Organize. Resolution rides the
  extraction pass that already reads the text, so it costs no extra model call.
- Resolution is deliberately cautious: it joins on a shared strong identifier
  (an email, a URL) or an exact normalized name within one project, and
  otherwise keeps entities apart. The two errors are not symmetric —
  under-merging leaves two nodes you can join later, over-merging fuses two
  people's records and once their mentions interleave nobody can say which fact
  belonged to whom.
- Every mention keeps the surface form as written, the record it came from, and
  why it resolved there. Merges are recorded rather than applied destructively
  and can be undone — "these are the same person" is a judgement, and a
  judgement nobody can take back is one people will not make.
- Entities live in Postgres, not a graph store, so traversal carries the same
  visibility predicate as retrieval. An entity with no visible mention does not
  appear at all, and counts report what the caller can see — "42 mentions" shown
  against a list of three is itself a disclosure.
- `entity_mentions` is covered by purge and by `verify_erasure`: a mention is
  personal data derived from a record.
- **The Prompts screen showed 6 data types while 24 were routed** — and its six
  mixed prompt names with data types. It now renders from `GET /api/v1/prompts`,
  which returns the register itself, including which prompts are shared and the
  extensions routing to each.
- **A rebuilt sign-in page.** It leads with numbers counted from the running
  build — 54 formats, 24 data types, 18 prompts, 9 webhook providers — and names
  the embedding model actually serving retrieval. A figure written into copy is
  wrong within a month and wrong in the flattering direction.
- `GET /api/v1/capabilities` is unauthenticated because the sign-in page has no
  session. It counts registries only; the route in front of it carries the
  platform identity token and never an API key.
- **The graph — typed edges and traversal**, in Postgres behind a `GraphStore`
  seam. `GET /entities/{id}/graph?depth=&predicates=`,
  `GET /entities/{id}/co-mentions`, `GET /graph/predicates`, and a Connections
  panel in the console with 1/2/3-hop controls.
- **Two kinds of connection, not merged.** An *asserted edge* is a claim a
  document made, carrying the records that assert it and how many — one document
  saying something is a claim, three saying it independently is closer to a fact.
  A *co-mention* is two entities named in the same record; it is not stored,
  because `entity_mentions` already records it and a copy would go stale.
- Co-mentions **need no model at all**, so the graph is useful the moment
  entities exist rather than only once extraction has read for relationships —
  which is the state the system is in whenever the extractor is degraded.
- Traversal carries the visibility predicate **inside the recursive query**. A
  path through a record the caller cannot read is never returned, because
  arriving at its far end would disclose that the record exists. Edges traverse
  in both directions — which end was written as the subject is a grammatical
  accident of the sentence.
- Relations ride the existing extraction pass, so no extra model call. A
  relation naming an entity the resolver did not produce is **dropped, never
  guessed at** — inventing an endpoint attaches a real claim to the wrong node.
  The predicate vocabulary is closed (12 values); `related_to` is the honest
  escape hatch, because a precise-looking wrong edge is worse than a vague right
  one.
- Erasure reaches edges: deleting a record deletes the claims it made, and
  `entity_edges` is checked by `verify_erasure`.
- `docs/graph.md` — why this is not a graph database, the vocabulary, visibility
  rules, erasure, a worked example, and what is not built.
- **A root README.** There was none — twenty-six commits of implementation and a
  visitor saw a directory listing. It leads with counted numbers, runs entirely
  locally with no cloud account, states the four commitments that are the actual
  reasons to choose this, and lists what is not built in as much detail as what
  is.
- **A comparison matrix** on the sign-in page: six products across eleven
  dimensions, with sticky tab navigation. Every mem-dog cell is verifiable in
  this repository; every other cell reflects what that product publicly
  documents, and where something is simply not part of a product's stated scope
  it is marked so rather than asserted absent — nineteen cells carry that mark.
- The **landing page** gains the graph and a competitor comparison. Every
  mem-dog cell is verifiable in this repository; every competitor cell describes
  what that product publicly positions itself on, never what it lacks. It ends
  with where the others lead — Zep's temporal facts, Mem0's adoption,
  Supermemory's latency, Letta's working context.

### Fixed
- **Self-hosting was presented as a differentiator**, which
  `docs/competition/README.md` had already researched and rejected: Onyx is
  MIT-licensed, air-gapped and SOC 2 Type II with 40+ connectors, Khoj runs
  fully local, and private deployment is table stakes here. The README and the
  landing page now say so explicitly rather than quietly dropping the claim.
- **The sign-in page navigation scrolled away.** It is sticky now — negative
  margins so the bar spans the full width rather than stopping at the text
  column, and `scroll-margin-top` so an anchor jump does not land with its
  heading hidden under the bar that took you there.
- **An artifact produced by a fallback engine was invisible to the reconciler.**
  It carries the primary's `generator_version` — correctly, since the prompt and
  schema were the primary's — so every staleness check considered it finished,
  and an item enriched during a provider outage would have kept its degraded
  summary forever. The real `fallback_depth` now lands on the artifact and the
  reconciler revisits anything a fallback produced.
- **The reconcile job was twenty image tags stale and had no `EMBED_ENGINE`**, so
  it re-embedded with the *old* model and concluded nothing was stale — a repair
  job quietly repairing the corpus back toward the state it was meant to leave.
  The deploy script never touched Cloud Run jobs at all, so the drift was
  structural; it now deploys them alongside the service, and creates a
  `memdog-crawl-tick` job too.
- **A rate limit consumed the retry budget**, so five refusals in a few hundred
  milliseconds dead-lettered work that was never faulty. A re-embed reported
  success having embedded almost nothing, leaving the corpus split across two
  vector spaces — the one state retrieval cannot recover from on its own. A busy
  provider is not a broken message; deferrals are now counted separately from
  attempts and are not bounded the same way.
- `.claude/skills/changelog` and this file.

### Changed
- **Quota is cost-weighted rather than counted.** A hundred vector searches and
  a hundred generations are the same number to a request limiter and three
  orders of magnitude apart in what they cost, so a limiter built on request
  count either throttles the cheap calls or admits the expensive ones. `/write`,
  `/retrieve`, `/ask` and the public `/webhooks/{producer_id}` are now charged in
  credits weighted by the work they authorise, and refuse with `429` and
  `Retry-After`. The webhook endpoint had no limit at all before.
- **The budget is checked before generation, not on arrival.** Retrieval is not
  where the money is, and refusing a cheap search to protect an expensive stage
  throttles the wrong thing — and does it after the search has already been
  paid for.
- **A budget refusal defers work instead of discarding it.** `BudgetExhausted`
  is classed with provider rate limits: the message is not faulty and will
  succeed unchanged once the window rolls, so enrichment stays queued and the
  item keeps its state rather than being dead-lettered.
- **New settings**, all lockable. **`budget_daily_credits`** — daily model
  spend, settable at platform, org, project and user scope, defaulting to no
  ceiling. **Every level binds and the tightest one wins**, so a project cannot
  raise the ceiling its organization set and a user cannot lift their own;
  ordinary most-specific-wins precedence would let exactly the party being
  limited do so. **`rate_limit_credits_per_minute`** (default 6000) and
  **`max_concurrent_requests`** (default 8), per credential — `0` disables
  either. An existing deployment behaves as it did until a ceiling is set,
  except that the burst limit now applies where there was previously none.
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
- `0023_usage.sql` — `usage_events` (raw, short retention) and `usage_spend`
  (the daily rollup enforcement reads, so a budget check is one indexed row
  rather than an aggregate over a table that grows with ingest). Run before
  deploying; the API writes to both on every model call.
- `0022_edges.sql` — `entity_edges`.
- `0021_entities.sql` — `entities`, `entity_mentions`, `entity_merges`.
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
