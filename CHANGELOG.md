# Changelog

What this repo became, for someone who was not here when it happened. The git
log records every change; this records the ones that alter what you can do, what
you must configure, or what was wrong before.

Newest first. Entries under `## Unreleased` have not been tagged.

---

## Unreleased

### Added
- **Bulk interpretation — `stage: "interpret"` on `POST /reprocess`.** `embed`
  and `enrich` rebuild derived work that already exists; this asks for work
  that was **never requested**, which is exactly what a crawl or a feed run
  with enrichment off leaves behind. It emits `enrichment.requested` per item
  rather than republishing onto a topic, and that matters beyond bookkeeping:
  **the reconciler repairs requested work and never invents it**, so a bulk
  interpret that skipped the log would be the one enrichment a dropped message
  loses for good.
- **A dry run returns evidence, not a number.** `by_state`, eight `samples`
  with their text, and `capped` when the selection stopped at ten thousand —
  because *"nothing else matched"* and *"we stopped looking"* are different
  facts. **4,212 reads identically whether the selector caught the crawl you
  meant or the whole project**, and the only way to tell is to look at a few.
- **A console screen for both** — *Interpret & rebuild*, under Lifecycle.
  `POST /reprocess` and `GET /artifacts/stale` had no caller at all, so the
  answer to *"interpretation is on now, what about the records already here"*
  was an API call typed by hand. Four tiles say what is behind (never
  interpreted · embedded but not summarised · built by an old generator ·
  done), and the run is **gated on a preview of that exact selector** — editing
  the selector drops the approval, since the cost is a model call per item.

### Fixed
- **Every webhook delivery landed unenriched, and no screen said so.** The
  producer's `defaults.enrich` governs it, defaults to `false`, and **nothing
  in the console could set it** — so a provider could post all day while
  Inbound reported healthy deliveries and **nothing it sent was findable**. The
  endpoint list now reads `interpreted` or `stored only` per row, which is the
  whole difference between two rows that otherwise look identical.
- **`budget_daily_credits: null` — "no ceiling" — could not be written at
  all.** asyncpg sends a Python `None` as SQL NULL without consulting the jsonb
  codec and `settings.value` is `NOT NULL`, so a project clearing an inherited
  budget got an integrity error from the driver. JSON `null` is a value; SQL
  NULL is the absence of a row, and `resolve` already reads presence.
- **`answer_storage` was documented with the wrong vocabulary.** It is
  `none` · `metadata` · `full` — the `CHECK` in `0001_spine.sql` and the
  comparison in `chat.py` — which writing the choices down is what caught.

### Added
- **The Settings screen writes.** It listed eleven settings with their values,
  provenance and lock state and could change none of them, which left
  **`enrich_by_default` — the switch deciding whether any write is interpreted
  — reachable only by calling the API by hand**. Scope is chosen per row rather
  than assumed, since the same key set for a user and for an org are different
  acts, and a refusal is shown as the server phrased it: *locked at org scope*,
  *must be one of invite_only, open, disabled*.
- **Settings are typed, and the type is enforced.** `put` accepted any JSON for
  any key, so `registration_mode: "opne"` stored cleanly and **matched none of
  the three branches that read it** — registration closes, nothing reports it.
  The register now declares `kind`, an enum's `choices` and whether `null` is a
  value; **refused, never coerced**, because `"true"` is not `True` and
  accepting both makes the stored shape depend on which client wrote it.
  `bool` is checked before `int`: in Python `True` **is** an `int`, and the
  obvious ordering stores `true` in a credit ceiling.
- **`GET /settings/effective` publishes that vocabulary** — `kind`, `choices`,
  `nullable`, `default` — so the console builds each control from the rule the
  server enforces. A dropdown whose options are typed out in the UI is a second
  copy, and the copy is the one that goes stale.
- **A webhook can ask for half of enrichment.** `defaults.embed` and
  `defaults.summarize` are honoured separately, because they are priced
  separately: embedding is one call per chunk and is what makes a delivery
  findable, summarising is one call per item. Reading only `enrich` made that a
  choice between an unbounded bill and an invisible corpus.
- **`GET /producers` returns `defaults`**, without which the console cannot
  show what it is about to change.

### Fixed
- **Nothing written from the console was ever searchable.** Enrichment is
  opt-in — `enrich_by_default` resolves to `false` and embedding runs only off
  `enrichment.requested` — and none of the three places the UI writes ever sent
  `options.enrich`. So an item added through Add data stopped at `stored`:
  durable, correct, and **invisible to retrieval, because no embedding was ever
  produced for it to match on**. The sandbox had it too, which means its whole
  write-then-find demonstration could only ever find nothing. **Browse had it
  worse** — an edit committed a revision and nothing re-read it, so search kept
  matching the text the edit had just replaced.
- **A screen watching that write could not say so.** It polled sixty times over
  two minutes, rendered nothing while it did, held the write button disabled
  throughout, and returned in silence whether the item had arrived or never
  would. **A deliberate refusal to spend money and a hang are indistinguishable
  that way**, and the refusal is the ordinary case.

### Added
- **The climb, rendered — and it always ends in a sentence.** Reached the top,
  refused by a sensitivity policy *with the reason*, failed after five dispatch
  attempts *with the error*, the bytes could not be read, or nobody asked. The
  panel reads `GET /events?data_id=…` beside the item rather than inferring
  from the rung, because **`searchable` is the same row whether the summary is
  queued, refused, or was never requested** and only the log separates them.
  The two-minute ceiling now says *this screen stopped watching; the work did
  not* — a different fact from settling, and it offers to keep watching.
- **Interpretation is configurable where it is caused.** `enrich`, `embed`,
  `summarize` and the two per-request overrides the API accepts and never
  persists, on the Add data screen — **on by default**, because a person adding
  one item by hand is not a producer pushing ten thousand. What the write will
  do is said next to each button (*"stored only — not searchable"*), since the
  panel sits below them and people write before they scroll.
- **The console can now enrich something.** `POST /data/{id}/enrich` had
  existed with no caller at all, so a corpus of items stranded at `stored` had
  no way out of the state it was in. Browse offers it per item; the progress
  panel offers it on the item just written.

### Added
- **`GET /api/v1/projects/{id}/source-lag`** — per scope: when it last
  succeeded, how far behind that is, what it last failed on, and whether its
  credential is cooling. **A connector that quietly stopped syncing looks
  exactly like a project that went quiet** — both are no new records, and a
  status signal computed over the first is confidently wrong. `cooling_until`
  is separate from `last_error` because a rate-limited source is not a broken
  one and does not need a person.
- **A crawl position per scope**, not per crawler. One crawler over forty Slack
  channels kept a single position, so a busy channel dragged it past thirty
  quiet ones and their history was never read. The cursor is **opaque** — a
  Jira cursor is a timestamp, a GitHub one an etag, a Salesforce one a
  `nextRecordsUrl` — and `scope = ''` is exactly the previous behaviour, so
  nothing needed migrating.
- **Rate-limit state on the connection, not the crawler.** Two crawlers sharing
  a Slack connection draw on the same quota and neither can see the other. A
  `429` parks the credential for as long as `Retry-After` asked rather than a
  guessed backoff, and everything sharing it waits. The scheduler reports a
  cooling credential **separately from `skipped`**, which means already
  running: both look like *did not run* and only one is a problem.
- **A run's counters move while it runs.** Emitted, skipped and failed are
  written every 25 items instead of once at the end, so a run in flight can be
  watched rather than waited on.

### Changed
- **A rate-limited run is no longer a failed one.** It ends with its own status,
  **does not move the cursor**, and the next run retries the same range. A
  scope that genuinely failed also keeps its cursor and records why — so a gap
  in the record reads as a failure and never as a quiet source.

### Fixed
- **A crawl that emitted for more than five minutes killed its own run.**
  `STALE_HEARTBEAT_SECONDS` is 300 and the reaper marks anything older
  `interrupted`, but the emit phase wrote no heartbeat at all. With `max_items`
  defaulting to 1000 that is the ordinary case for a real source, not an edge,
  and it presented as noise: runs randomly interrupted, a position that never
  advanced, and a next run that re-fetched everything.

- **A run that hit its item budget was filed as a scope failure**, so a crawler
  that had just emitted a thousand items reported `last_ok_at: null`,
  `items_seen: 0` and an error — reading as a source that had never once
  worked. That is precisely the false alarm source lag exists to prevent,
  inverted. Two questions were conflated: *how far can the next run safely
  resume from* and *is this source healthy*. A capped run answers the first
  with "no further" and the second with "yes", so it now records the items it
  moved and clears the error while leaving the cursor where it was.

- **Enabling a crawler made it due immediately whatever its schedule said**, and
  the scheduler's selection never checked the type — so a crawler someone had
  deliberately marked `manual` was picked up and run by the platform scheduler
  before they ever triggered it themselves. Enabling a manual crawler now makes
  it *runnable*, not due.

### Migrations
- **`0039_sync_state.sql`** — `crawl_cursors`, and `limited_until` /
  `last_limit_reason` on `connections`. Additive; existing crawlers keep their
  position as `scope = ''`.

### Not built
- **No connector template declares an incremental clause**, so all 37 still
  full-scan. The machinery above is wired and tested; the templates have not
  been filled in, and until they are the recorded cursor is never fed back into
  a request.

### Added
- **Alerts can be described in words, not only as a selector.** `mode: "llm"`
  judges what the conditions let through — **one call per run over the whole
  batch**, never one per event, which is what makes it affordable at all. The
  conditions stay **required** in this mode: without them every transition in
  the project would reach a model. If no model is available the run **defers
  rather than guessing** — extraction falls back to a heuristic because a worse
  summary is recoverable, but a wrong verdict is a false alarm or a silence
  nobody notices. A candidate the model does not mention is **not matched**.
- **Conditions are generic.** A field name *or a dotted path* into the event,
  with `in` · `not_in` · `eq` · `ne` · `contains` · `gt` · `lt` · `exists`. A
  payload shape nothing has seen before is reachable, so a new kind of event
  needs no new vocabulary — and a path into nothing is **false, never an
  error**, because one odd record must not stall a batch.
- **An alert can be scoped** to one memory, case, producer or entity. Separate
  from the conditions on purpose: those ask about the event, a scope asks
  whether the subject is yours at all — and a transition does not know which
  memory its item is in, so it is a join. Applied after the selector and before
  any model, one query per scope key over the batch.
- **The backtest returns what it would have caught**, not just how many. A count
  is not calibration: a selector that matches everything looks identical to one
  that works until you read what it matched.
- **The console's Alerts screen is a monitoring surface.** Three tabs — the
  definitions, what they caught, where events get sent. Counts by state double
  as filters, every row leads with a state dot and a strip of its last twenty
  runs, and the unseen count is on the row: for an alert, *stopped* and *nothing
  to say* otherwise produce identical silence. Create and edit are one form, so
  the two cannot drift into disagreeing about what an alert can be.
- **`GET /api/v1/alerts/surfaces`** also serves the operator and scope
  vocabularies, so a console never holds a second copy that can drift from what
  the server validates.

### Changed
- **The sign-in page no longer claims mem-dog cannot answer what was true in
  March.** That was honest when written and became false the day the temporal
  graph shipped. The concession to Zep is narrowed to what still holds —
  multi-hop traversal over time — and an alerting row is added to the
  comparison, an axis it did not have.

### Fixed
- **A duplicate alert name was a `500` with an empty body.** Names are unique
  per project so a feed cannot confuse two alerts; reusing one is an ordinary
  mistake and is now a `409` that says so.
- **An alert's "unseen" count included the whole event log** — every write and
  every enrichment request, traffic no alert watches — so each one showed a
  permanent backlog and the number meant to say *the sweep has stopped* said
  nothing. It counts transitions of the alert's own kind now.
- **`alert_runs.model_calls` did not exist in any already-migrated database.**
  It was added by editing `0034` after `0034` had been applied, and
  `schema_migrations` records the version, so the edit reached nothing —
  production answered every backtest with `column "model_calls" does not exist`.
  **A migration is immutable once applied**, and this cannot fail locally: the
  suite drops the schema and re-migrates every run, so it always reads the
  edited file and passes.

### Migrations
- **`0036_alert_scope.sql`** — `alerts.scope jsonb`. Additive.
- **`0037_alert_run_cost.sql`** — `alert_runs.model_calls`, with
  `IF NOT EXISTS` so a database created from the briefly-edited `0034`
  converges rather than failing.

### Added
- **Compaction — fold a memory down without losing any of it.** A job is a
  memory, an algorithm and a schedule: `POST /api/v1/compaction/jobs`. It
  **archives** what it folds, so records leave the default view and stay
  readable, searchable and citable via `?include_archived=true`. mem0
  reconciles by overwriting; this cannot, because the temporal graph shipped on
  the premise that a claim is *closed* rather than replaced — a compaction that
  destroyed its inputs would make `as_of` lie about everything it touched.
- **Two algorithms, and the cheap one is first.** `dedupe` archives members
  byte-identical to a newer one and **needs no model** — most of what a corpus
  accumulates is the same record written twice. `summarize` folds members into
  one artifact and **refuses rather than degrading** when no extractor is
  configured, naming `dedupe` as the alternative: a summary produced by a
  fallback heuristic is a worse summary presented as the same thing.
  `GET /api/v1/compaction/algorithms` serves the list with `needs_model`.
- **Previewing is a gate, not a courtesy.** `POST /compaction/jobs/{id}/preview`
  runs the live path with its writes withheld; scheduling is a **409** until
  this version has been previewed, and editing what a job would do drops the
  approval and stops it. A compaction nobody has looked at is one that empties a
  memory quietly.
- **Runs record what they cost and freed** — considered, archived, artifacts,
  bytes before and after, and `model_calls` (always `0` for `dedupe`).
- **`GET /api/v1/data/{id}/versions/{version_id}`** returns one revision with
  its text **in full**. The listing previews at 400 characters on purpose, so
  the whole text is a second request made only for the revision chosen — and it
  **404s** for a caller who cannot read the item, because a revision of an
  invisible record must not be confirmable.
- **The console browses by drilling down** — a memory, a page of items, one
  item, one revision — instead of loading fifty items with every revision
  expanded. Audit groups by action with counts that double as filters, one log
  at a time, detail on the row you open.

### Changed
- **Two guarantees on a compaction summary**, both invisible if broken. It takes
  the ACL of its **most restrictive** source, or compaction becomes a way to
  widen visibility by summarising. And it records **span offsets** per source,
  so a citation opens at the sentence — without them every citation in a
  compacted memory silently degrades to a document-level reference.
- **Archival is a column, not a state.** An archived item is still `stored`,
  `searchable` and `enriched`; it is merely not current. Folding the two
  together would make "is this searchable" and "is this in the working set" one
  question.

### Migrations
- **`0038_compaction.sql`** — `data_items.archived_at` and `archived_by`,
  `compaction_jobs`, `compaction_runs`. Additive. Scheduled jobs ride the
  existing minute sweep rather than adding a fourth Cloud Run job.

### Not built
- **TTL is still not enforced.** `memory_types.ttl_seconds` and `on_expiry` are
  stored and `effective_expiry()` is computed, but nothing sweeps — a
  `conversation` memory with a one-hour TTL is still there next year, and
  `orphan_delete` and `archive` have never run. Compaction is explicit and
  scheduled; expiry is a separate mechanism that does not exist yet.

### Added
- **Five more alert surfaces**, all deterministic: `data.revised`,
  `memory.member_added`, `memory.retyped`, `case.member_promoted` and
  **`acl.changed`**. The last could not have been done any other way — the
  statement that changes a record's level destroys the evidence that it
  changed, since `RETURNING` after `ON CONFLICT DO UPDATE` reports the new
  value. The upsert now reads the prior level from the same unique index it was
  about to probe.
- **Three of them are mostly about refusing to fire.** A revision whose content
  is byte-identical is not a change — a re-crawl and a re-parse produce them
  constantly — and the first revision is skipped because the write already
  announced that record. Re-adding an item already in a memory is not a
  membership event, and landing in the `default` memory is not one either: that
  is where an unattached item goes, which is the absence of a signal. A second
  document agreeing with a fact is a corroboration, not an assertion.
- **`data.revised` carries the old and new `source`.** `write` and `reprocess`
  mean the upstream document changed; `parse` and `interpret` mean the same
  bytes were read better. Only the first is a change in the world.
- **Evaluation is asynchronous and debounced.** A consumer wakes on a
  transition and then waits, coalescing before evaluating once — two hundred
  messages inside a window produce at most one run. Nothing in the consumer is
  the record; the watermark is, so a window lost with its instance costs
  latency rather than an alert.
- **Outbound delivery, which memdog has never had.** `POST
  /api/v1/event-subscriptions` registers an **https-only** endpoint; deliveries
  are signed HMAC-SHA256 over `{timestamp}.{body}` — the same scheme the
  inbound path expects — retried with backoff, then **dead-lettered visibly**
  and replayable. The signing secret is shown once and can be rotated, never
  read back; the previous secret keeps verifying for an overlap.
- **The subscription URL is validated on every attempt, not just at
  registration.** A host that resolved to a public address yesterday can
  resolve to a private one today, and this service reaches Cloud SQL over the
  VPC. Redirects are not followed at all. A delivery whose recipient has since
  lost sight of the subject is dropped rather than retried.
- **An Alerts section in the console**, in its own group. It enforces rather
  than displays: the enable button is disabled until this wording is
  backtested, editing drops the approval visibly, deferred work is shown in run
  history, and the signing secret says plainly that it will not be shown again.
- **[docs/alerts.md](docs/alerts.md)**, and the sign-in page now describes the
  two clocks the temporal graph shipped with.

### Changed
- **The README's counts were wrong.** 112 endpoints and 596 tests are 134 and
  630; 58 modules across 32 migrations are 60 across 35. `GET
  /api/v1/capabilities` now also reports `alert_surfaces`, counted from the
  vocabulary, so a surface added without being documented still shows up.

### Migrations
- **`0035_event_delivery.sql`** — `event_subscriptions`, `event_deliveries`.
  Additive. A subscription requires an **owner**: delivery is filtered by that
  owner's rights at send time, and one without an owner is a notification
  channel with no access control.

### Added
- **Alerts — declare an event worth knowing about, and be told when it
  happens.** `POST /api/v1/alerts` names a **surface** (a kind of transition)
  and a **selector** over it; matches are recorded and read back from
  `GET /api/v1/events?since=<sequence>`. First surfaces are the fact
  transitions: `fact.asserted`, `fact.superseded`, `fact.retracted`. The cursor
  is a sequence and not a timestamp, because two events in the same millisecond
  would otherwise come back in whichever order the planner liked, and a poller
  re-reading from a time would skip one.
- **Evaluation is per window, never per write.** Alerts read forward from a
  watermark rather than being triggered by each write — N alerts by M writes
  means every write pays for every alert, and one crawl importing ten thousand
  items would trigger ten thousand rounds. Five enabled alerts cost a write
  zero evaluations, which the suite asserts rather than the comments claim.
- **A new alert starts at the head of the log, not the beginning.** One that
  fires a hundred notifications about last month the moment it is saved is one
  somebody switches off.
- **Enabling requires a backtest of the current version** — `POST
  /api/v1/alerts/{id}/backtest` runs history through the live path with its
  writes withheld, so what it reports is what a live run would do. Editing what
  matches bumps the version, drops the approval and **disables the alert**;
  renaming does none of those. Refusing to enable an un-backtested alert is a
  409.
- **A capped batch reports what it deferred.** Silent truncation reads as
  "nothing else matched", which for an alert system is the worst available lie.
  The watermark carries the remainder to the next run, and advances only after
  matches are written — so a crash cannot step over transitions nobody looked at.
- **`memdog-alert-tick`**, deployed with the API and driven by a **one-minute**
  Cloud Scheduler job. The reconciler's ten minutes are fine for enrichment,
  where lateness costs nothing a user sees; an alert ten minutes late is a
  different product.
- **`llm` mode is refused with a 501.** The schema and the surface are designed
  for it, the evaluation is not built, and accepting it would look like a
  working alert that ignores its own description.

### Changed
- **Fact transitions are recorded in `domain_events`** as `fact.asserted`,
  `fact.superseded` and `fact.retracted`, carrying subject, predicate, object,
  basis and both entity types. Emitted inside the transaction that made them,
  because a transition is observable only while it happens — once a row reads
  its new value the old one is gone. A second assertion of a claim already held
  is a corroboration and **does not** emit `fact.asserted`, or an alert watching
  assertions would fire every time another document agreed.
- **An event carries no access level, deliberately.** Visibility is the
  subject's, resolved when someone reads: a copy taken at match time is stale
  the moment the record is re-shared, and ignores a revocation in between. A
  notification is the one side channel around every other access check.

### Migrations
- **`0034_alerts.sql`** — `alerts`, `alert_runs`, `observed_events`. Additive.
  Transitions deliberately have **no table of their own**; they are
  `domain_events` rows, which already carry the sequence an alert reads forward
  from.

### Added
- **The graph answers *what was true when*, and *what we believed when*.** Facts
  now carry two clocks: `valid_from`/`valid_to` for the world, and
  `recorded_at`/`retracted_at` for us. `GET /api/v1/entities/{id}/graph` and
  `/retrieve`'s graph arm take **`valid_at`** and **`as_of`**, independently —
  a document imported today about last year is visible at `valid_at=last year`
  and **invisible** at `as_of=last month`, because we had not read it yet. Both
  default to now, so nothing an existing caller does changes. Valid time comes
  from the record's `event_time`, never `now()`; a backfill that used ingestion
  time would land every historical import as breaking news.
- **A claim can be superseded instead of accumulating.** `located_in` and
  `reports_to` hold one open value, so moving to Berlin closes living in Lisbon
  — writing `valid_to`, never deleting, so an earlier `as_of` still returns the
  graph as it stood. Everything else accumulates, and **`works_for` is
  deliberately multi-valued**: people hold two jobs, and marking it single would
  quietly close every second one as though they had left. `GET
  /api/v1/graph/predicates` now serves the single-valued list, since a caller
  writing facts needs to know which of theirs will close another.
- **Facts can be asserted with no document and no model.** `POST /api/v1/facts`
  records a claim directly — until now `entity_edges.source_data_id` was NOT
  NULL, so an agent that already knew something had to manufacture a document
  for an extractor to read it back out. `basis` (`asserted` | `derived`) keeps
  the two apart. `POST /api/v1/facts/{id}/retract` withdraws one without erasing
  that it was made.
- **`GET /api/v1/graph/conflicts`** surfaces single-valued predicates holding
  more than one open value — a document and a later thread disagreeing, as a
  query rather than a model call. Two claims beginning at the same instant are
  deliberately *not* resolved: picking one would be a guess wearing the clothes
  of a fact.
- **`GET /api/v1/entities/{id}/history`** — every claim that has touched an
  entity, closed and open alike, with its windows, evidence count and the reason
  for any retraction.

### Changed
- **Erasing the last evidence for a derived fact retracts it rather than
  deleting it.** Deleting would erase that we ever believed it, which is what a
  bitemporal table exists to preserve; leaving it open would assert a claim with
  nothing behind it. `verify_erasure` now checks that no derived fact is open
  without evidence. An **asserted** fact survives item erasure — it never
  depended on a record.
- **The docs no longer promise Graphiti.** `comparison-onyx.md` claimed
  `valid_at`/`invalid_at` "via Graphiti", `technology.md` gated a temporal store
  behind an `is_graphiti_enabled()` that does not exist in the code, and
  `architecture.md` drew Neo4j in the diagram. The capability is real now and it
  is Postgres. An external store stays possible behind `GraphStore`, but must
  first carry the ACL predicate *inside* its traversal — post-filtering a graph
  discloses the shape of what it hid — and be reachable by `verify_erasure`.

### Migrations
- **`0033_temporal_graph.sql`** — adds `entity_facts` and backfills one fact per
  existing edge group, then sets `entity_edges.fact_id` **NOT NULL**. An edge is
  evidence for a claim; one written without a fact is silently invisible to the
  traversal, so the constraint is structural rather than conventional. Additive
  and self-contained: no row is deleted, and the ULID helper it needs is dropped
  at the end of the migration.

### Fixed
- **`deploy/smoke.sh` failed against healthy deployments.** It sent no
  `options.enrich`, so the write fell back to the project's
  `enrich_by_default` — off, because enrichment is optional by design. The
  items landed in `stored` and stayed there, and the script then asserted
  retrievability it had never asked for, printing `FAIL: nothing retrievable`
  with **nothing in the logs to contradict it**. That silence is the
  diagnostic: an enrichment that errors leaves a trace, one never requested
  leaves none. A `Pending` item remaining `stored` is correct and still shows
  up under `excluded` on a passing run.
- **A deletion time range could not be sent over HTTP.** `since` and `until`
  reached asyncpg as strings, which it refuses for a `timestamptz` — so the
  time_range selector raised a 500 for every caller, and since a JSON body
  cannot carry a datetime, the selector was unusable rather than awkward. It
  survived because the one test that passed a `since` also passed an invalid
  `time_clock` and raised on that first, never reaching the query. Parsed now
  where the selector is interpreted, so the seed's reset gets it too. A naive
  instant is read as **UTC, not server-local**; an unparseable one is a 400
  naming the field rather than a 500.
- **`metadata` on a write item was accepted and thrown away.** The field has
  been in the contract since the spine shipped, the write-api example shows
  `{"tags": ["source:salesforce"]}` in it, and nothing read it — there was no
  column. So every producer following the documented shape lost its tags, and so
  did the crawler, which built `crawler:<id>`, the item's title and its source
  URL into exactly that field. Nothing errored: the write succeeded, the item
  was durable and searchable, and only the provenance was gone. It is stored
  now, and a `tags` key inside `metadata` is **lifted into the `tags` column**
  so the old shape works — merged with the top-level field rather than replacing
  it, and deduped. Still never consulted for access control; the ACL is sealed
  before any caller-supplied value is read.
- **Crawled items carry `crawler:<crawler_id>` and their configured tags again.**
  `CrawlerConfig.tags` was a documented, user-facing field that did nothing.
  `GET /api/v1/data/{id}` now also returns `metadata` and `run_id`.

### Changed
- `memdog-seed` is deployed by `deploy/cloudrun.sh` rather than created by hand.
  A job pinned to whichever image was current the day someone made it drifts —
  which is how the reconciler ended up twenty tags behind — and the seed drives
  the API in-process, so a stale one seeds a corpus the running service would
  not have produced. It takes its own resources and **no retries**: a retried
  seed finds the org the first attempt created and fails with that as its reason.

### Migrations
- `0032_item_metadata.sql` — `data_items.metadata jsonb NOT NULL DEFAULT '{}'`.
  Additive, no backfill: records written before it keep the empty default, and
  the provenance they lost is not recoverable from the item. Re-crawling
  repopulates it, since `external_id` upserts.

### Changed
- **The README leads with evidence instead of claims.** Rewritten from a blank
  page: it opens with a real `/retrieve` response from a running deployment —
  `matched_by` per hit, the records considered and dropped with their reasons,
  the corpus state counts, the generator fingerprint — then the same record id
  fetched with two keys, 200 and 404. Both captured live, neither invented. The
  features table is gone (a catalog is not a reason to choose something) and four
  diagrams became two. 325 lines → 246.
- **The README is rewritten against what the build reports.** Four counts had
  drifted in a file whose own second paragraph says they come from the running
  build; they now match `GET /api/v1/capabilities`. The `excluded` paragraph had
  been duplicated near-verbatim one section apart and is said once. Three things
  it was quiet about are now stated: **enrichment is optional** and its diagram
  implied otherwise, `docs/usage.md` exists, and **no deterministic foreign-key
  edges** joins the not-built list.

### Added
- **A deploy runbook, checked against the live project rather than transcribed.**
  `.claude/skills/deploy-gcp/` holds the routine deploy and its failure modes,
  and a from-scratch guide in the one order that works — the peering range
  before the database, because org policy forbids a public IP and the range
  cannot sit inside the auto-mode network's own `10.128.0.0/9`. Reading it
  against `memdog-dev-506718` corrected four things the scripts and
  `deploy/README.md` did not say: **`GEMINI_API_KEY` is required** and was
  undocumented, so a project provisioned from the prose starts cleanly and then
  fails every enrichment; `storage.objectAdmin` is granted **on the bucket, not
  the project**; **no `roles/cloudsql.client`** — it is for the auth proxy, and
  there is none; and `memdog-bootstrap` is unmanaged, pinned to `spine-12`, and
  runs `grant-key` rather than a bootstrap. Failure modes are indexed by
  symptom, since the reader has an error message and not a diagnosis.
  `CLAUDE.md` requires it be corrected in the same session the process changes.
- **[docs/usage.md](docs/usage.md) — six scenarios against a running system.**
  Write and ask, pull from an app, receive a webhook, build the graph, backfill a
  crawl that ran with `enrich` off, and erase with a dry run first. Every request
  in it was issued against a live deployment, which is how the `/ask` filter shape
  and the deletion selector rules got in. Includes the readiness staircase and a
  troubleshooting table for the commonest report — *it returned nothing* — which
  is almost always items sitting in `stored`.
- **`reprocess` selects on `run_id` and `tags`.** This is the point of the
  repair above. `enrich` is off by default on a crawler, so the intended
  sequence is crawl → read the dry run's count → enrich what it found; but
  `stale_only` and `stale_generator` both match on an existing artifact, which a
  never-enriched item does not have. The one corpus that default produces was
  the one corpus reprocess could not reach, short of enumerating ten thousand
  `data_ids`. `tags` matches on **overlap, not containment** — "any of these",
  which is the question people ask.
- **Workday, Dynamics 365, and five more CRMs.** The CRM shelf held five entries
  and was missing the one most people name first. Nine catalog entries added —
  **Microsoft Dynamics 365** (any Dataverse table), **Close**, **Copper**,
  **Freshsales**, **Zendesk Sell**, **Capsule**, **Affinity**, and Workday
  twice. **37 entries, 36 usable**; Zoho CRM remains the only blocked one. None
  is verified, as before — the dry run is still where an entry stops being a
  researched guess.
- **Workday is two entries, and they are not the same promise.** *Workday
  (custom report)* reaches a RaaS custom report with an integration system user
  over basic auth, which is how bulk data actually leaves Workday and needs
  nothing switched on. *Workday (workers)* uses the REST API and
  `client_credentials`, and is conditional: the grant has to be enabled on the
  API client, and some tenants permit only the JWT bearer grant, which is not
  one of the six styles here. The entry says so rather than failing at the token
  request with no explanation.
- **Where only the operator knows the id, the form now asks for it.** Dynamics
  and the Workday report both declare an **ID column** scope, because Dataverse
  names a primary key after its singular table (`accountid`, `contactid`) and a
  Workday report names its columns after their labels. A guessed id produces a
  crawler that hashes every row into a fresh record on the next run, which reads
  as duplication rather than a missing field.

### Changed
- Two of the new entries **say in their notes that they pull one page**, rather
  than looking complete and quietly truncating: Dynamics pages with a whole
  `@odata.nextLink` URL and Copper pages inside the request body, and the
  crawler's pagination templates only the query string.

- **Google and Microsoft, without a person in the loop.** "They need OAuth"
  stood here for weeks and was only ever true of a *person* connecting their own
  account. An organization connecting its own data uses a grant with no human
  step at all, which is a POST. Two exchanged auth styles now do it:
  **`client_credentials`** (Microsoft Graph, Salesforce, Zoom, Xero) posts
  `client_id:client_secret` to a token endpoint, and
  **`google_service_account`** signs a JWT assertion with the key file. Both
  take their non-secret settings — token endpoint, scopes, an optional delegated
  `subject` — in the new **`auth_config`** on `POST /api/v1/connections`, which
  is reviewable in full because it holds no secret by construction.
- `subject` on a Google connection is **domain-wide delegation** — the assertion
  says which user it is acting as, which is how one credential reads many
  mailboxes. It is never set by default: an assertion that impersonates by
  default is one nobody chose.
- **A fourth crawl strategy, `tree`.** Listing one folder is one request; a
  document library is a tree. It walks Google Drive or Microsoft Graph
  breadth-first, bounded by `max_depth` and the run's budget, skipping folders
  already visited so a Drive shortcut cannot turn the tree into a cycle.
  `include_mime` is an allowlist of prefixes; empty means every file, which is
  usually not what anyone wants of a shared drive.
- A tree walk **emits references, not documents**: each file becomes a `Pending`
  ref naming the same connection, and the fetch worker resolves it. That puts
  the download where the byte cap, the blob store and the parse pipeline already
  are, rather than inside a discovery pass holding a run open.
- **The fetcher understands `google_drive` and `microsoft_graph`.** Google's own
  formats have no bytes to serve — `?alt=media` on a Doc is a 403 — so Docs and
  Slides are exported as text and Sheets as CSV; that export format is the
  decision about what gets indexed. A Google thing with no export (a form, a
  shortcut) says which it is rather than returning the source's 403.
- Three catalog entries for walking rather than listing: **Google Drive (folder
  tree)**, **SharePoint (library tree)**, **OneDrive (drive tree)**. The pair is
  not redundant — a listing stores what a folder contains, a tree stores what
  the documents say, and choosing between them is choosing between a file index
  and a corpus. **Zoho CRM is the only genuine OAuth case** and stays listed
  with that reason attached.
- `GET /api/v1/capabilities` reports **`connectors`** and
  **`connectors_available`**. Both, because the difference is the honest part: a
  count that silently dropped the blocked entry would read as complete coverage.

- **`memory_links` works.** Declared with the memories migration and reached by
  nothing — no function, no endpoint, no reader — so a memory could never be
  said to `continue` another or be `derived_from` the conversations it
  compressed. `GET/POST/DELETE /api/v1/memories/{id}/links` now do, and reads
  return **both directions separately**, because "what is derived from this?"
  and "what is this derived from?" are different questions and merging them
  loses the direction that is the whole claim.
- Links carry **`created_by`** (`explicit` / `routed` / `agent`) and a
  `confidence` for the derived ones. A person restating what an agent guessed
  promotes the link; an inference never overwrites a statement. An explicit link
  refuses a confidence outright — a statement is not 80% true.
- **A crawler can authenticate.** The templated `http` strategy already covered
  enumerate, query and search for most REST APIs; it could reach only *public*
  ones, because a crawler had nowhere to keep a secret and the only place to put
  a token was its config, in the clear. `POST /api/v1/connections` registers an
  enveloped credential and `PATCH /api/v1/crawlers/{id}/connection` points a
  crawler at it — which is the distance between three public feeds and any API
  with a token.
- **Four auth styles, closed**: `bearer`, `header` and `query` (each with a
  name), and `basic`. APIs differ here far more than they differ in pagination,
  and small enough to be data. The credential is applied **last**, so a config
  cannot override it — a templatable `Authorization` would be somewhere to put a
  secret in the clear again. `header` and `query` refuse without an `auth_name`
  rather than guessing `X-Api-Key`, since a secret sent to a header the source
  ignores fails as a wrong *credential* instead of a wrong *configuration*.
- A credential is written and **never read back**. `GET /api/v1/connections`
  reports whether one is held, never a prefix — a prefix is enough to confirm a
  guess. Having no connection is not a degraded case: a sitemap needs nobody's
  permission.
- **The console can ask for the graph arm.** Search gains a segmented control
  over the arms — it reads like tabs and behaves like a set, because `match` is
  a list and the graph arm earns its keep by being *fused* with the others. It
  refuses to go all-off, since a search with no arms is an error rather than a
  narrower search.
- **The seeds are shown above the results they explain**, and clicking one opens
  that entity. The Entities panel gains **Search from here**, which returns to
  Search with the name filled in and the graph arm switched on — that being the
  question it is asking.
- **The graph is a retrieval arm.** `match: ["vector", "lexical", "graph"]` on
  `/retrieve` and `/ask` adds a third arm, fused by the same reciprocal rank
  fusion as the other two and reported in `matched_by` as `gph`. It returns what
  neither other arm can: a search for *"Priya Raman"* finds the quarterly
  revenue note, because a different record said Priya works for Northwind and
  the note names Northwind. Nothing in that note matches the query, which is the
  point.
- **`graph_seeds`** on both responses names the entities a query resolved to and
  how. A graph-only result contains none of the words searched for, so without
  the seed a reader cannot tell whether the connection found was the one they
  meant — and an empty list says the arm found nothing to *start* from, which is
  a different answer from finding nothing connected.
- The arm is **opt-in, not a default**. It answers a different question from the
  other two and is only as good as the entity layer beneath it: with extraction
  degraded to the local heuristic there are no entities, so it correctly returns
  nothing. It walks **one hop** and returns each connected record's opening
  chunk — it claims the *record* is connected and has no view about which
  passage answers the question.
- Two disclosure rules are enforced inside the SQL rather than after it. **An
  edge is traversable only when the record asserting it is readable** — walking
  first and filtering after would still surface the far endpoint, and the
  existence of a connection is itself what the unreadable record's ACL protects.
  **An entity seeds only through a readable record**, since resolving against
  the entity table alone confirms a name exists in this project to somebody who
  can see no record containing it.
- **Three wiring guards** in `tests/test_wiring.py`: every settings key is read
  somewhere, every public function is referenced somewhere, every schema column
  is named somewhere. Six defects in one week shared the shape of something that
  existed, was documented, was correct, and was reached by no code path — none of
  which errored. The guards found seven more on their first run.
- Eight schema columns are now **documented as unwired rather than silently so**
  — `memory_links` end to end, the model-proposal inputs, and an
  `allow_public_sharing` flag superseded by the setting that actually gates
  sharing. A parametrised test expires each exemption: wire one up, or drop it
  from the schema, and the list is required to change with it.
- **Model cards declare `hosting`** (`local` or `remote`), which is what makes
  the residency rule checkable. Deliberately not derived from `provider`:
  provider is who made the model, hosting is where the bytes go, and Ollama is
  the same adapter against a local process and against Ollama Cloud. A card that
  does not say defaults to `remote`.
- **`GET /api/v1/models` reports `violations`** — assignments that already exist
  and would now be refused. Reported rather than voided, because retroactively
  invalidating what a deployment is running takes a service down to enforce a
  control it did not know it was breaking.
- **Registration is closed by default, and an invite is how the second user
  arrives.** `POST /api/v1/invites` issues one, `GET` lists them with their
  state, `DELETE` revokes before redemption, and `POST /api/v1/invites/redeem`
  — **unauthenticated**, because whoever is redeeming has no account yet —
  exchanges the single-use token for an API key scoped to the invited role.
- An invite is a bearer credential and is treated as one: hashed at rest, shown
  once at creation, expiring in 7 days by default, revocable, and **bound to an
  email address unless `transferable: true` is passed explicitly**. A link bound
  to nobody is a link anyone can forward, so opting out of that is a decision
  rather than a default.
- **Invites are audited on creation and on redemption.** Who invited them and
  who walked through the door are different questions, and a forwarded invite
  answers only the second. Neither record contains the token.
- Every redemption failure returns the same sentence. Expired, revoked, already
  redeemed, wrong address, never existed — the distinctions are real and all of
  them disclose whether an organization exists.
- **`python -m memdog seed --demo` — a demo tenant, and the end-to-end check the
  repo did not have.** Forty records about one Acme renewal, written through the
  public write verb with a registered producer, enriched synchronously, in a few
  seconds against the local engines. It is not a fixture: a seed that inserts
  rows tests the seed, and diverges the moment the real path changes.
- **The seed verifies itself and names the step that broke.** It asks the corpus
  five saved questions and requires each to return the record that answers it,
  requires a second member's search *not* to return the record written through a
  personal connection, requires the access log to have rows after those reads,
  and requires every embedding to record its model. Each is one clause of the
  Phase 1 exit criterion, so a green seed is that criterion demonstrated rather
  than asserted — and `tests/test_seed.py` runs it on every commit.
- **`--reset` purges through the ordinary delete cascade** — the same
  selector-delete an offboarding uses, narrowed by the `demo` tag because a
  project id alone is deliberately not a selector. It also recovers a half-built
  demo, since an interrupted seed leaves users behind with no org to hold them
  and the alternative is a hand-written `DELETE` against a live database.
- Demo credentials are **generated per deployment and printed once**. A known
  demo user with a known password, present in every install, is a shipped
  default credential.
- The demo also **registers a normalization schema and writes two structured
  payloads through it** — one that projects cleanly and one missing a required
  field, which lands raw with a reason. A demo where everything worked teaches
  an expectation the user's own corpus will not meet, and the gap then reads as
  the product failing rather than as normal.
- The demo ships **one domain rather than the six the design calls for**.
  Clinical, legal, support, telemetry and personal each demonstrate a mechanism
  sales cannot, and four of those mechanisms are only partly built — seeding a
  domain to demonstrate something absent produces a demo that lies. The
  deferrals are listed in [onboarding.md](docs/operations/onboarding.md) rather
  than left to be discovered.
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
- **A permission failure is no longer a 500.** Ten handlers call
  `actor.require()` in their own body and are not wrapped by `_control`, so a
  credential lacking a capability escaped as `500 Internal Server Error` —
  telling whoever looked that the server was broken when the truth was that
  their key could not do this. An app-level handler now translates `AuthError`
  from anywhere, registered once rather than fixed in ten places.
- **A model card naming a provider with no engine builder** could be listed,
  selected and assigned, then resolved to nothing and fell back to the
  deployment default — no error, a plausible answer, and the chosen model never
  ran. A guard now refuses any shipped card the engine layer cannot construct,
  and a second refuses one declaring a capability that is not a purpose.
- **The console could not reach three endpoints it was built to show.** They
  were missing from the UI proxy's allow-list — the browser's only route to the
  API — so `GET /api/v1/connectors`, `GET /api/v1/connections` and
  `PATCH /api/v1/crawlers/{id}/connection` were refused with a 403 the panel had
  no way to report. The app catalog rendered as an empty category list, which is
  indistinguishable from a catalog that is genuinely empty.
- `GET /api/v1/projects/{id}/entities` was allow-listed **without a query
  string**, so the panel worked until somebody applied a filter. That is the
  harder version to notice, and `npm run check:proxy` now catches it: a guard
  that walks every call site in the console and fails on any path the proxy
  would refuse. Typecheck and build cannot see this, and did not, four times.
- **The MCP panel was refused for the same reason.** Its path is now allowed for
  `GET` only. `GET /api/v1/mcp` is a manifest that discloses nothing; `POST` is
  a tool call, and the proxy replaces the caller's credential with the console's
  own — so allow-listing `POST` would publish an unauthenticated MCP server.
  **The endpoint the panel prints therefore does not yet work for an external
  client.**
- **The crawler preset row offered three of the four strategies.** `tree` is now
  there as *Drive folder*, with a lower depth and item cap than the default.
  Each preset carries its own input placeholder: the seed for a folder walk is
  an id, and labelling it "Seed URL" is how somebody pastes the wrong thing.
- **The console could not create either exchanged credential**, so the Google
  and Microsoft catalog entries were unreachable from the UI that listed them.
  The Credentials form now offers both styles, asks for the token endpoint and
  scopes they need, takes a service-account key file as a paste-in block rather
  than a password field, and sends `auth_config`.
- **A source's 401 now drops the cached token.** An exchanged credential is held
  until shortly before it expires, so a source that started refusing — consent
  revoked, a scope changed, the secret rotated at the provider — went on being
  refused with the same dead token for up to an hour after somebody fixed it,
  and the fix looked like it had not worked.
- **A download no longer forwards its credential across a redirect.** Both Drive
  and Graph answer a download with a 302 to a pre-signed CDN URL, and the
  `Authorization` header followed it — handing an access token to a host that
  never needed one, which is the ordinary way a token ends up in somebody else's
  logs. It is stripped on any cross-host hop.
- A resource id is **pattern-checked before it is put in a URL**. It arrives
  from a listing and goes straight into a request path, so a slash or a
  dot-segment in it is a path traversal against an API — and works exactly as
  well as one against a filesystem.
- A token endpoint answers a bad secret with a body that quotes back what it was
  sent. **That body never reaches an exception or a log**, which is otherwise
  the ordinary way a credential ends up somewhere durable.
- **The strategy taxonomy in the docs said six; the implementation has four.**
  `enumerate`, `query` and `search` differ in pagination shape and field names,
  not in kind, and all three are `http`. `docs/ingestion/crawlers.md` and the
  README now say four.

- **An enrichment failure was retried or discarded depending on the record's
  id.** The check was `"429" in str(exc)` — a substring search over the
  exception message. ULIDs are base32, so roughly one record in a few hundred
  carries those three characters, and `data_01M1785DBZKP726EV0429YK0H0` was
  enough to make a defect look like a provider quota and be deferred forever.
  Classification is now by exception type and HTTP status; a `400` is no longer
  retried because it happens to mention a number.
- **`allow_public_sharing` is gone.** It shipped in the first migration carrying
  FR-ACC-4 and was read by nothing — the rule is enforced by the
  `public_sharing` setting, which also carries the precedence chain, the lock
  semantics and an audited write. An admin who found the column and set it true
  had turned on nothing.
- **Model spend is attributable to the run that produced the record.**
  `usage_events.run_id` existed from the day the meter shipped and nothing
  populated it: a crawl run knows its own id when it writes, but the record had
  nowhere to carry it, so by the time enrichment spent money the connection was
  gone — and a dry run's estimate could never be checked against an actual.

- **The graph arm read `entity_edges` directly instead of going through
  `GraphStore`.** It passed every behavioural test — including both about
  disclosure — while making the seam a lie: swapping the store would have moved
  the Entities panel and left search reading Postgres. It now hands its seeds to
  `neighbourhood` and only fetches chunks for the entities it is given, so the
  rule that an edge is traversable only when the record asserting it is readable
  lives in one place rather than two. Endpoints pass the configured store rather
  than letting the arm build one. The cost is a query per seed instead of one
  fused query, bounded by the eight-seed cap.

- **The test suite would drop whatever database `DATABASE_URL` happened to
  point at.** Every `pool` fixture begins with `DROP SCHEMA public CASCADE`, and
  the only thing choosing the target was `os.environ.setdefault` — so an
  exported `DATABASE_URL`, of the kind anyone running a deploy or opening a
  psql session has, silently became the thing that got dropped. It cost a
  seeded corpus in development this week, which then looked like the API being
  broken rather than the tests having wiped it. Against the production instance
  the same command would have dropped the corpus.
- The suite now runs only against a host that is obviously local, and names the
  database it refused. **A disposable database elsewhere — a CI service
  container — needs `I_KNOW_THIS_DATABASE_IS_DISPOSABLE=yes`**, a variable named
  so that setting it is a sentence about that database and not something anyone
  exports for another purpose.

- **A graph-only search whose query named no entity returned a `500`.** With no
  seeds the arm built no SQL, so the "at least one match mode is required" guard
  fired on a perfectly ordinary question. Finding nothing to start from is an
  empty result — and the search still runs through the query row and the audit,
  because a search that found nothing is still a search that happened.
- **The arm chips were hardcoded to vector and lexical** in both the console and
  the sandbox, so a graph-matched result would have rendered with every chip
  dark — a hit that appears to have matched nothing at all. They now show the
  arms the last search actually ran, and distinguish *not asked for* from *asked
  for and did not match*.

- **Raw usage rows were never purged.** `purge_events` was implemented and
  tested and called by nothing, so the table grew without limit while the
  retention story read as done. It now runs on the reconcile sweep, bounded by
  the new **`USAGE_RETENTION_DAYS`** environment variable (default 90; `0`
  disables it for a deployment that ships rows elsewhere first). The daily
  rollup is untouched.
- **`enrich_by_default` was a switch wired to nothing.** The register declared
  it `True`; the write contract hardcoded `False`; nothing read the setting. A
  project that turned it on got silence. The register now says `False` — which
  is what actually shipped, rather than switching every deployment's spending on
  to match a document — and `enrich` on a write accepts **`null`**, meaning
  "ask the project". An explicit `true` or `false` still wins.
- **A webhook signature verifier the request path had stopped calling**, with
  three tests still pointed at it. Signing moved per-provider and the old copy
  stayed behind; the live check is `providers.verify`, which those assertions
  now run against. A passing test over dead code is worse than no test, because
  it reports on a scheme the service does not use.

- **A regulated record was still summarised by whatever the deployment
  configured.** The candidacy rules below gated model *assignment* and the image
  path; enrichment consulted none of them, and enrichment is where the whole
  corpus goes. A `clinical_note` was sent to the deployment's extractor — a
  cloud provider in the shipped configuration.
- A regulated record now **narrows the extraction chain to its locally-hosted
  steps** rather than being refused. The floor is a local extractor, so the
  record still gets a title and a summary and simply never reaches an engine
  that would have received its text — the same reasoning the chain already uses
  for availability, applied to legality. Where no step survives, the record is
  left unenriched and an `enrichment.refused` event says why.
- The narrowed extractor carries **its own `generator_version`**. Reusing the
  primary's would attribute a locally-produced envelope to the model that was
  refused, which is the staleness-invisibility defect a fallback artifact
  carrying the primary's fingerprint already caused once.
- **Six metrics were emitted and never registered, so every measurement was
  dropped.** `record()` returns quietly for a name it does not know — right at
  the call site, wrong across a release, because a counter that silently goes
  nowhere is indistinguishable from one that is genuinely always zero, and
  always-zero is what an operator reads as *good*. The four `usage.*` counters
  shipped with the meter, `enrich_refused` shipped with this change, and
  `entity_mentions` had been dark for longer. A test now walks every `record()`
  call in the package and fails on any name the registry does not carry.

- **A regulated data type could be assigned to a cloud model.**
  `data_type_profiles.sensitivity` shipped with the catalog, carrying its own
  comment that a clinical or legal type must not be routed to an unapproved
  provider — and assignment validation selected the column beside `requires` and
  used only `requires`. `clinical_note` ships as `regulated`; assigning it to a
  cloud model succeeded.
- **`allowed_providers` was never read.** The settings register describes it as
  how "only our approved providers" is enforced rather than suggested; it
  appeared in one comment and nothing else. It is now exhaustive once set —
  an *empty* list still means "no list", because an empty list forbidding
  everything would break every deployment that never set one.
- Both rules are checked at **assignment** and again at **resolution**. An
  assignment made before the rules existed would otherwise still route content,
  and the deployment default was checked by nothing at all — so a regulated type
  with no assignment went wherever the deployment happened to point.
- **The parse worker checks the item's own sensitivity before handing bytes to a
  model.** Resolution keys on the modality, and a clinical note that arrived as
  a scan is a regulated record *and* an ordinary image — checking only the
  modality sent it to a cloud vision model, because `image` is standard. A
  refusal is recorded as `needs_model` with the reason; the record is still
  stored and readable, it simply has no transcript.

- **A registered normalization schema was never applied.** `POST /api/v1/schemas`
  stored one and `normalize.project()` knew how to run it, but nothing on the
  write path called it — so `normalized_records` stayed empty and `identifiers`
  was only ever what the writer restated, which for a structured record is
  nothing: the sender posts a payload, not a list of keys. It now runs on the
  write path where there is text, and after parse where the text arrives as
  bytes.
- **The projection runs before correlation, not after.** The identifier a case
  joins on lives inside the payload, so projecting second would correlate on
  nothing — the ordering the previous code would have had, if anything had
  called it.
- **Projected identifiers are merged onto the item rather than assigned.** One
  the writer supplied is a fact they know and the schema does not, so a schema
  extracting a single field no longer silently drops the rest.
- `normalize.project()` takes a connection rather than a pool, so it shares the
  caller's transaction. `normalized_records` references `data_items`: on its own
  connection a projection either raced the insert it describes or survived a
  write that rolled back.
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
### Removed
- Four unreachable functions: a crawler scheduling helper superseded by inline
  logic in `crawling.py`, an id utility, an accessor added with the meter and
  never used, and the webhook verifier above. The dangerous one is always the
  duplicate — it is the copy someone fixes by mistake.

### Changed
- **A model assignment now governs extraction and answering, not just images.**
  `resolve_model` was consulted from one place — the multimodal path — so the
  catalog, the assignment endpoints and the candidacy rules applied to image
  interpretation and nothing else, while extraction, answering and embedding
  each ran on whatever an environment variable built at boot. An org that
  assigns a model for `extraction` now gets it, per data type, resolved per
  request.
- The `engines` row an assignment names is finally **read**: provider, base URL,
  and the credential that had been encrypted there since the first release and
  used by nothing. Clients are cached on `(engine_id, model_id, kind)`, so two
  orgs on the same model share one and an org assigned a different engine can
  never be handed it.
- **An org that assigned nothing is unaffected** — it gets the same object it
  got before, not a reconstruction. A disabled engine or a provider with no
  implementation falls back to the deployment default and logs. A *candidacy*
  refusal is raised instead: falling back on a regulated type assigned to a
  remote model would route the content the rule exists to protect.
- **Answering follows the extraction assignment** rather than becoming a purpose
  of its own, because chat was already tied to that configuration so an org
  makes one `allowed_providers` decision rather than two. The coupling is now
  per-org instead of per-deployment; it is not looser.
- **Embedding is deliberately still a deployment decision.** Two orgs extracting
  with different models produce artifacts that each record which model made
  them, which is recoverable; two orgs *embedding* with different models write
  vectors from different spaces into one index, and the only signal is that
  ranking quietly gets worse.
- **A new identity no longer becomes a `users` row just for authenticating.**
  `registration_mode` is enforced where the account would be created, not only
  where membership is granted: `disabled` refuses outright, `invite_only`
  admits an address that is already a user or holds a live invite, and `open`
  behaves as before. Existing users and existing members are unaffected; what
  changes is who can newly appear.
- **`python -m memdog bootstrap` refuses once any user exists**, with a message
  saying so and pointing at invites. It creates the first admin and only the
  first — an exception that can be taken twice is an unauthenticated
  account-creation endpoint wearing an operations script's clothes. The
  library function is deliberately not guarded, because the seed and the test
  fixtures use it and both carry their own guards.
- **New setting `registration_mode`** (platform and org scope, lockable,
  default `invite_only`). A deployment that wants self-service must now say so.
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

### Migrations
- `0031_tree_strategy.sql` — adds `tree` to the `crawlers.strategy` CHECK. Run
  before deploying; a `tree` crawler cannot be stored without it.
- `0030_token_exchange.sql` — adds `client_credentials` and
  `google_service_account` to the `connections.auth_style` CHECK, and
  **`connections.auth_config jsonb`**. Run before deploying. Existing
  connections are unaffected: `auth_config` defaults to empty and the four
  presented styles do not read it.
- `0029_memory_links.sql` — `created_by`, `confidence` and `created_at` on
  `memory_links`, plus the reverse index.
- `0028_item_run.sql` — `data_items.run_id`. Set by `write_items` as an
  argument, never as a field on the request: attribution anybody can assert is
  attribution that cannot be reconciled against an estimate.
- `0027_drop_allow_public_sharing.sql` — drops the column. **If a deployment
  ever set it, check the `public_sharing` setting**, because setting it did
  nothing.
- `0026_crawler_connections.sql` — `crawlers.connection_id` and the
  `auth_style` / `auth_name` a connection presents its credential with. The
  foreign key is **`ON DELETE RESTRICT`**: removing a connection out from under
  a running crawler would leave it enabled, scheduled, and failing every tick
  with an authentication error — nothing errors loudly and the data simply
  stops arriving.
- `0025_hosting.sql` — `model_cards.hosting`, defaulting to `remote`, with the
  two shipped local engines corrected by name. **Review your model cards after
  deploying**: any card an operator registered is now declared remote, so a
  locally-hosted model needs saying so before it can serve a regulated type.
- `0024_invites.sql` — the `invites` table. Run before deploying; the registration
  check reads it on every first-time sign-in.
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
