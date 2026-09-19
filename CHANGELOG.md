# Changelog

What this repo became, for someone who was not here when it happened. The git
log records every change; this records the ones that alter what you can do, what
you must configure, or what was wrong before.

Newest first. Entries under `## Unreleased` have not been tagged.

---

## Unreleased

### Added
- **The graph drawing is back, beside the lists.** It was removed when it could
  not name anything; the cause was the node-shape mismatch, not the drawing, and
  with that fixed the same layout labels its nodes. The picture carries the shape
  of the corpus and the lists carry everything it cannot fit — selecting anything
  drives both.

### Removed
- **Cases is gone from the console**, along with the "Subject" picker on *Add
  data* that fed it — nothing on this deployment has ever written a case, so
  both were controls over an empty table. The API, the write path and the
  asserted-vs-inferred membership machinery are untouched; a producer can still
  declare a case.

### Changed
- **`Your data` is now three subsections** — *Set up*, *Put it in*, *Read it* —
  rather than seven items in one list.
- **The `Sources` group is gone**, folded into *Put it in*: `Inbound` and
  `Crawlers` are how data arrives without a person present, and having them
  elsewhere split "how does data get in" across two parts of the sidebar.
- **`Repositories` is now `Repository analysis`**, which is what the screen does
  — adding a repo lives in `Add data` with everything else.

### Fixed
- **The Graph screen showed relationships and no entities.**
  `GET /api/v1/projects/{id}/graph` returned nodes as `entity_id`/`display_name`
  while the public graph route returned `id`/`name`, and the shared component
  reads the latter — so every node lookup missed and every label was skipped.
  Predicates were unaffected because they ride on the edge and need no lookup,
  which is why relationships stayed visible throughout. Both routes now return
  one spelling, with a test on it.

### Changed
- **The Graph screen is two lists, not a drawing.** Entities ranked by how many
  claims touch them, and the claims themselves gathered under the kind of
  relationship they are; selecting an entity narrows both. The canvas is gone —
  it could not name more than a fraction of what it drew, and a list can name
  all of it.

### Added
- **The Graph screen says what kind each relationship is.** A counted, glossed
  chip per predicate above the drawing — each one a control that narrows to it —
  and the claims below gathered under their kind rather than listed flat. The
  drawing showed that things were connected and never how, which is the part a
  typed vocabulary exists to express.

### Changed
- **Chat asks one thing instead of five.** Scoping is now the memories you pick,
  combinable or all of them. The topic, lens and entity-anchor filters and the
  "follow connections" switch are gone from the console; `entity_ids` and
  `template` still work on the API.
- **Retrieval always runs all three arms.** The graph was opt-in; it fails to
  nothing — it starts from an entity your question names, contributes nothing
  when it finds none, and costs a bounded traversal rather than a model call —
  so the choice had one sensible answer and has been removed.
- **A memory's keywords and entities are description, not controls.** They were
  buttons that added filters and looked like data; now they are data.

### Added
- **An answer says which relationships the graph arm crossed.**
  `Answer.graph_relations` and `RetrieveResponse.graph_relations` — subject,
  predicate, the server's gloss for it, object, how many records assert it, and
  whether it is structural or a reading. Previously `_expand` kept only the
  reachable entity ids, so a record reached only through the graph arrived with
  no account of why it was there.

### Added
- **The Graph screen narrows to a memory or a single record.** `memory_id` and
  `data_id` on `GET /api/v1/projects/{id}/graph`, both asked of the *evidence* —
  so a claim several records support appears under each of them — and both
  carrying the visibility predicate, or a filter would confirm that a record you
  cannot read sits in a named memory.
- **Edges are labelled with their predicate.** Not all of them: forty labels do
  not fit beside forty-five names, and the handful that land read as though the
  rest had no predicate. Following a node labels *its* edges, which is the
  question the control is being used to ask anyway.

### Added
- **The Graph screen has a project picker.** The console is otherwise pinned to
  `MEMDOG_PROJECT_ID` for every section, so looking at another project's graph
  meant redeploying the UI. Reading is widened on its own here rather than in the
  shell: a console-wide switcher would have to carry `MEMDOG_PRODUCER_ID` with
  it, and a producer belongs to one project — switching the shell would point
  *Add data* at a producer that is not in the project on screen.

### Fixed
- **The graph drew a dozen disconnected islands on a real project.** Claims were
  ordered by how many records assert them, which grades well on a demo corpus
  and not at all on an ordinary one — 91 of 92 edges were asserted exactly once,
  so the ordering did nothing and the first forty claims were an arbitrary sample
  from across the corpus. Ties now break on how connected the endpoints are,
  which draws the corpus's actual clusters instead.
- **Long entity names went unlabelled.** A name can be a whole sentence; at the
  drawing's size a 43-character one is half the canvas wide, so it was rejected
  everywhere and its node left anonymous — losing precisely the most specific
  names. Truncated in the drawing only; the claims list and the accessible name
  carry the full text.
- **The busiest node was the one most likely to be unnamed**, because a hub is
  ringed by its own neighbours and every position around it is taken. A name can
  now be drawn over its own node as a last resort.
- **Labels could be painted over by nodes drawn after them**, so a centred hub
  label lost its opening characters. They are their own layer above every shape
  now.

### Added
- **A Graph screen in the console, under *Your data*.** What this project
  asserts, drawn best-attested first and then listed as sentences — *"Priya
  Raman is employed by Northwind Trading · 3 records"*. Selecting anything
  narrows the drawing and the list together. Filters for the lens a record was
  read under, the relationship, and how many claims to fetch; the lens list and
  the predicate glosses both come from the API rather than a copy in the page.
- **`GET /api/v1/projects/{project_id}/graph`** — a whole project's current
  claims, rather than a walk outward from one entity. Claims are selected and
  nodes follow: ranking entities by mentions and drawing what joins them
  produces a picture of the cast, and the relations between the most-mentioned
  things are the ones a corpus states least often. Takes `limit`, `predicates`,
  `template`, `valid_at` and `as_of`; both endpoints of every edge are joined
  inside the query, so an edge is never returned pointing at a node the caller
  was not given.

### Fixed
- **Three things in the graph drawing that only rendering it revealed**, now
  fixed once in a component both the landing page and the console use: hub nodes
  were dragged into the middle of their own neighbours until the graph was a
  knot; arrowheads inherited `markerUnits="strokeWidth"`, so the best-attested
  claims — drawn thickest on purpose — got heads three times the size of the
  node they pointed at; and labels were tested for collision against other
  labels but not against nodes, so a name could render straight through the
  shape beside it.

### Changed
- **`seed-demos` takes `--only=<key>` and seeding one corpus is now the normal
  way to use it.** The seeder deletes each corpus's project before writing it, so
  an unfiltered run takes the whole published gallery down and builds it back —
  and a run that runs out of quota partway leaves behind only the corpora it had
  already deleted. A filtered run prints just the entries it seeded, which must
  be **merged** into `PUBLIC_DEMOS`; setting the variable to that output
  unpublishes everything not named in it.

### Fixed
- **A large corpus could not be seeded at all.** Three limits sized for a hundred
  records, all of which the 701-verse corpus hit: the seeder failed on the write
  rate limit (`6000 credits/minute`) instead of waiting out `Retry-After`; the
  in-process drain gave up after ten minutes, though enrichment is one worker per
  topic and therefore serial; and every Cloud Run job carried a 30-minute task
  timeout. The seeder now backs off, the drain defaults to two hours
  (`DEMO_SEED_DRAIN_SECONDS`), and `--task-timeout` is per job — three hours for
  `memdog-seed`, unchanged for the ticks. **Raise the drain and the task timeout
  together**, or the job is killed inside the drain and leaves a project written
  and half-enriched.

### Migrations
- None. A seed of the scripture corpus is roughly seven hundred model calls and
  over an hour of wall clock, and it rests entirely on the vector arm — the
  lexical arm returns nothing for a natural-language question, because
  `websearch_to_tsquery` ANDs every word of it. Check `claims` on
  `GET /api/v1/public/demos` after seeding: an entry reporting few or none is
  enrichment having fallen back to the local heuristic, not a quiet corpus.

### Added
- **The Bhagavad Gita, as the first corpus in the gallery you can *look* at.**
  The demo card gained a second tab beside the chat: the claims a model actually
  extracted, drawn, and then listed as sentences — *"Krishna puts forward
  detachment · 9 verses"*. Selecting anything narrows both halves to it. The
  other four corpora each demonstrate something about retrieval; this one is the
  only place the knowledge graph — half of what the write path builds — is
  visible at all.
- **701 verses, one record each, read under the `scripture` template.** The
  citation is `BG 2.47`, the reference the commentary tradition already uses, so
  an answer is checkable by the reader most likely to check it. The template is
  what makes the graph worth drawing: open-domain extraction over the same text
  yields `related_to` edges saying two words occurred nearby, while the declared
  schema records BG 2.62–63 as a chain of directed `leads_to` edges you can
  follow from the senses to ruin.
- **`GET /api/v1/public/graph?demo=<key>`** — unauthenticated, like the rest of
  the demo surface, and deliberately narrower than it. It takes no traversal
  parameters at all: no root, no depth, no predicate filter, no clock. Narrowing
  the picture to one relationship is done in the page. No record id, record text
  or corpus count is returned. It is **not** charged against the daily question
  budget — no model is called to draw it — and is cached for five minutes
  instead, which is the bound that replaces metering.
- **`graph.overview(project_id)`** for signed-in callers too: a whole project's
  current claims, best-attested first, rather than a walk outward from one
  entity. Both endpoints are joined inside the query, so an edge is never
  returned pointing at a node the caller was not given, and an entity whose every
  mention sits in a record you cannot read is not named.

### Changed
- **`GET /api/v1/public/demos` now reports `claims` per corpus.** The page uses
  it to decide whether to offer the graph tab at all — a corpus written without
  enrichment has no graph by design, and a tab opening onto an empty box reads as
  a broken feature rather than an absent one.

### Migrations
- None, and no new environment variable — but **nothing new is public until
  `PUBLIC_DEMOS` is reset.** `python -m memdog seed-demos` writes the corpus and
  prints the registry to set; it fetches the text at seed time and **fails rather
  than publishing** if any sample question has stopped finding its verse. Budget
  for one model call per record on the enrichment pass.

### Added
- **Connect a Google Drive folder by sharing it with an address.** *Add data* has
  a sixth kind: copy the address the console shows, share the folder with it in
  Drive, paste the folder's link. Every document under it, subfolders included,
  is downloaded and parsed — Docs, Sheets and Slides exported as text — so what
  lands is answerable with citations rather than a list of filenames. It replaces
  the old route, which was to create a service account elsewhere, download its
  key and paste that.
- **The crawler arrives switched off and the dry run is the point.** A folder
  nobody shared authenticates perfectly and returns nothing, so a dry run
  reporting **zero is the answer, not a failure** — it is the only signal that
  the share step was missed, and it has to arrive before anything is ingested.
- **A folder belongs to the first project that connects it**, and a second is
  refused with `409`. One identity reads every folder shared with it and a folder
  id lives in a URL, so without this, knowing an id would be enough to read
  another tenant's documents. Reconnecting a folder you already have returns the
  existing crawler rather than a second one.

### Migrations
- None. **A new optional secret**: `memdog-drive-key`, a service-account JSON key,
  mounted as `DRIVE_SERVICE_ACCOUNT`. `cloudrun.sh` binds it only when the secret
  exists, because `--set-secrets` fails on one that does not. Without it the
  feature reports itself unconfigured and everything else is unaffected — see the
  `deploy-gcp` skill for switching it on.

### Added
- **A use case on the signed-out page for the thing that speaks first.** The
  other ten are all somebody asking a question; standing queries and alerts
  appeared on the site only as two labels inside the architecture diagram. This
  one draws what the Monitor half of the console actually does: a rule written
  once that watches *forward* from a watermark and sees each record exactly
  once — never a re-scan; a deadline as a second kind of rule over the record's
  own date, because nothing arrives on the day one approaches; a selector that
  narrows nothing refused outright, since its feed would be a copy of the
  project; and delivery checked under the owner's own visibility **at match
  time** rather than rights copied when the rule was written, because a match
  handed to somebody who cannot see the record leaves the system through the
  notification channel. Delivery is a feed you poll or a memory you can then
  question — there is no webhook delivery for a standing query; that is event
  subscriptions, which is a different primitive.
- **Cases is a screen again**, under *Your data* — the other way to walk a
  corpus. A memory answers how long something matters; a case answers what it is
  about, and a patient, a legal matter or an asset outlives every conversation
  filed under it. Declare a subject on an authoritative identifier (an MRN, a
  matter number, an asset tag) together with the other identifiers it is known
  by, list what exists, and open one history. The screen was removed in September
  as a third description of how the corpus is arranged; this is the subject half
  of the story instead.
- **A history says how each record got onto it.** `asserted` — a producer said
  whose it is — reads differently from `inferred`, which carries the identifier
  it matched on and its confidence. Collapsing the two is how a timeline
  silently comes to contain somebody else's records, and nothing downstream can
  detect it.
- **Ordered by when it happened, not when it arrived**, oldest first. An undated
  record is stated rather than quietly filed under today, and the API's
  500-record ceiling is said out loud — a clinical or legal history truncated in
  silence reads as a complete one.
- **Add data step 3 is now *Where it goes, and what it is about*.** It files the
  record against a subject, takes the identifiers written on the record, and
  takes **when it happened** — so a scanned, forwarded or backfilled document
  lands where the event belongs rather than at the top of the chart. Records
  join a subject at write time, so without this the new screen could only ever
  show an empty timeline.
- **A `subjects` tile on Overview.** The API had always counted cases; the tile
  was removed because there was nowhere for the number to go.
- **A patient-timeline use case on the signed-out page.** Four records arrive
  from four systems; three join on the record number and the fourth — a letter
  carrying only a name — attaches to nothing rather than to a guess, because two
  patients share a name and that merge is coherent, checkable and undetectable.
  Then the history on an axis, with a discharge summary scanned last Tuesday
  sitting in 2019 where the care actually happened.

### Fixed
- **A document large enough to need an upload session lost its subject and its
  date.** `POST /api/v1/uploads/{id}/complete` forwarded `access`, `template`
  and `options` and dropped `case`, `identifiers` and `event_time` — so the same
  file inlined attached to its patient and kept its date, while the uploaded one
  arrived detached and dated on arrival. Nothing raised: the record was real and
  simply missing from the history it belonged to.
- **A Sign in link that pointed at nothing while the demo gallery loaded.** The
  header and the hero disagreed about which layout was being drawn for the
  moment the gallery was in flight, so the header emitted an anchor to a hero
  that was suppressed — and the sign-in card rendered in neither place.

### Added
- **The eleven diagrams in the published docs are diagrams now.** They were
  being served as their own mermaid source — `flowchart LR` and a list of node
  definitions, set in a code block — because the markdown renderer turns every
  fence into `<pre><code>`. They had never rendered in the UI at all, and it
  read as documentation written that way rather than as a bug.
- **`npm run diagrams`**, which renders them. Mermaid measures text to lay a
  diagram out and so needs a real browser; this renders once through headless
  Chrome and commits the SVG to `lib/docs-diagrams.ts`, keyed by the diagram's
  own source. **Run it after changing a diagram in `docs/` and commit the
  result** — `npm run verify` now fails and names the document otherwise. A
  changed diagram cannot serve a stale picture: the key misses and it degrades
  to the code block it used to be. `npm run verify` itself needs no browser.

### Changed
- **Every diagram on the site now sits on a near-black slab**, the same in light
  and dark theme, with black boxes and light borders. The docs diagrams are
  rendered ahead of time with their colours baked in and cannot answer a theme
  toggle, so the choice was one constant ground for all of them or half the
  diagrams refusing to follow the theme. The hand-drawn landing diagrams were
  moved onto the same palette, so generated and hand-drawn read as one thing.
- **`npm ci` installs 155 more packages** — mermaid and its tree, all
  `devDependencies`, pinned so a re-render reproduces byte for byte. It lands in
  the Docker builder stage, not the served image; the client bundle is unchanged
  at 109 kB, because the SVG is inlined server-side.

### Added
- **A classroom use case on the signed-out page** — the marks a teacher writes
  down anyway, read back to the student. A diagnostic, exit tickets and a unit
  test become checkpoints on one student's timeline, and the same record answers
  two questions that are not the same question: the teacher's, where one
  misconception recurring across three lessons is visible in the third lesson
  rather than in December's report card; and the student's, where the answer is
  a direction rather than a verdict — 4/10 to 9/10, the sentence that says what
  changed, and a citation opening the piece of their own work it came from. One
  student and one skill on purpose: a whole-class dashboard is the picture every
  school product already draws.
- **A Zoom recall use case on the signed-out page.** Zoom posts a signed webhook
  when a recording is ready — a real path, with its own signature scheme and
  handshake in `providers.py` — and the transcript joins every earlier instance
  of the same standing meeting. Ask the series a question and the answer carries
  the passage and the day it was said; and because the meeting recurs, the same
  series answers "what changed since last week".
- **A `© buildgeek.ai` notice**, on the landing page footer and at the foot of
  the console sidebar. The year comes from the clock, not a literal.
- **A rental-property use case on the signed-out page** — what asset-mem.com is
  built on, and the clearest case for change detection the page has. A unit is
  inspected at move-in, quarterly and at move-out; each report is a checkpoint.
  Ask what happened during the tenancy and you get three changes, the
  maintenance history including a faucet that broke and was repaired; ask what
  is different since move-in and you get one, because the faucet ends where it
  started and the carpet does not. Two numbers, two conversations, and each is
  obviously right for its own question.
- **Asking a memory what changed across a span, from the console.** The range
  endpoint had shipped with no UI at all — you could see a timeline but not ask
  the question a timeline exists for. The memory screen now has a since/until
  pair and a churn-or-net toggle, and the result says which of the two questions
  was answered, which checkpoints in the window contributed nothing and why, and
  whether the ask spent a model call.
- **Two more diagrams on the signed-out page: a researcher, and a repository.**
  Both features existed only behind sign-in, so the two most distinctive things
  you can point mem-dog at were invisible to anyone deciding whether to try it.
  Each is drawn around what it refuses — the researcher diagram's spine is the
  identity gate, with the refusal in a box of its own, and the repository
  diagram says outright that each snapshot is pinned to its commit and compared
  to nothing, because "four reports" invites the assumption that two of them can
  be read against each other.
- **A researcher's papers can be added from Add data.** Paste a Google Scholar
  profile URL and it resolves to an OpenAlex author, downloads the open-access
  PDFs of their works, reads and indexes them into a memory of their own keyed
  to the author — a corpus you can ask questions of with citations. The API
  could already do this; the only route to it was the Crawlers screen, which you
  had to know about. The result is not a success message but the name,
  affiliation, works count and what the match rested on, because two researchers
  share a name and the wrong one yields a corpus that is coherent and about
  somebody else with nothing downstream able to detect it. The crawler is
  created switched off, and `How to use this` carries the whole flow as a worked
  example.
- **The landing page has two tabs and three diagrams of what the thing is
  for.** *Try it* and *How it works*, so a phone gets a screen rather than a leg
  of a long scroll — the demo no longer sits between a visitor and the first
  picture, nor the pictures between them and sign-in. The strip appears only
  when there is a demo to choose between. Under *How it works*, the existing
  system diagram is followed by three use cases, each drawn around the one
  mechanism that carries it: **a company brain**, where the point is the
  citation going back to the record rather than the answer; **a timeline
  memory**, where the same report arrives weekly and the two readings of a span
  disagree on purpose — four changes composed against nothing net, the numbers
  the service actually returns for that span; and **agent memory**, where a
  later session reads what an earlier one wrote instead of replaying a
  transcript.
- **`?net=true` answers what is different between two points, net of everything
  in between.** The composed range reports churn — green, red, green is two
  changes; the net range compares the description at each end directly and the
  same three values come back as nothing. Both are correct answers to different
  questions, which is what `basis` has been there to tell you. One model call,
  stored in `memory_change_ranges` and keyed on the **generator version** as
  well as the two ends, so a moved prompt misses the cache rather than being
  answered from it; the response carries `cached` so a caller can see whether
  this particular request spent anything. A net comparison is refused without a
  `from` — "since the beginning" has no description to compare against — and
  refused between two ends described by different generator versions, which is
  reported through the same `gaps` vocabulary the composed range uses.
- **A checkpoint timeline can be asked about a span, not just a step.**
  `GET /api/v1/memories/{id}/changes?from=&to=` composes the deltas already
  stored across a window, so it costs nothing and every claim in it is backed by
  an artifact a citation can open. `from` and `to` each take a checkpoint id, a
  sequence number or an ISO timestamp; a timestamp lands on the last checkpoint
  at or before it, and the window is exclusive of `from` and inclusive of `to`
  so two ranges chained together neither overlap nor skip. Three fields keep the
  answer honest: **`basis`** says how it was reached and is never inferred —
  composing reports *churn*, so green, red, green is two changes, while the net
  reading is a different question that costs a model call and is not built yet;
  **`gaps`** names every checkpoint in the span that contributed nothing and why
  (`not_checked`, `failed`, `incomparable`, `not_visible`), because a range that
  omits what it could not read presents as complete; and an **inverted window is
  refused** rather than answered empty, which would have reported "nothing
  changed" about a span that was never examined. A span over 500 checkpoints is
  refused rather than truncated. Checkpoint timelines are now documented in
  `docs/memories.md`, which had no section on them at all.
- **`checkpoint.checked` — an alert surface for a check that ran and found
  nothing.** Every check on a checkpoint timeline now reaches the event stream,
  not only the ones that found movement, carrying `status`, `outcome`, `reason`
  and the same delta digest as `checkpoint.changed`. It is deliberately the
  noisiest surface here: a consumer advancing a watermark needs "we looked and
  it had not moved" to be a message rather than a silence, and the checkpoint
  row has always drawn that distinction while the stream did not. The alert it
  exists for is `{"outcome": ["incomparable"]}` — a timeline still accepting
  records while every comparison refuses, which was visible only by eye in the
  console and is the failure here that looks most like health.
- **The competitive comparison is published**, at `/docs/compare`, with `How it
  compares` beside `Docs` in the header. Four documents: the landscape, and
  head-to-heads with Onyx, Glean and the "company brain" category. They are
  worth reading because they are not marketing — the landscape retires one of
  this project's own claims ("self-hosted and air-gapped… is table stakes in
  this category"), and says plainly that Onyx inherits each document's source
  permissions and mem-dog does not, so if that is the requirement today then
  Onyx is the better answer today. Each carries the month it was researched,
  because the claims are about other people's products as those products
  documented themselves at the time. The company-brain comparison carries a
  section saying where its own confidence is thin.
- **The public page carries a gallery of demo apps rather than one corpus.** Each
  is a seeded use case with its own description, its own sample questions and its
  own chat, and none of it is reachable until `PUBLIC_DEMOS` names it — a request
  supplies a *key*, resolved server-side, so a corpus nobody published cannot be
  asked for. The existing single-corpus configuration keeps working as a
  one-entry registry. **The daily allowance is shared across the gallery**, not
  one budget per app, and it is shown beside the ask button rather than
  discovered at zero. Three corpora ship: a legal matter (asserted vs inferred
  membership), a four-week meeting series (what changed since last time), and a
  cold-chain sensor fleet (63 raw readings that never reach a model, and the 3
  derived digests that do). `python -m memdog seed-demos` seeds them and prints
  the `PUBLIC_DEMOS` line to set; it deliberately does not write it anywhere.
  A fourth was added afterwards and is the only one that is not invented: **a
  real researcher's papers**, resolved from a public Google Scholar profile and
  fetched from OpenAlex at seed time rather than copied into the repository.
  Nothing clinical is included. The synthetic marker is applied per corpus, not
  unconditionally — stamping "generated, not real" across a real researcher's
  abstracts would be a false claim about work that exists.
- **The documentation is readable without a checkout**, at `/docs` — 24 curated
  documents as static routes, in a shell with a sticky bar and a contents column
  that stays beside you as you read, marking the page you are on. Below the
  width where a column stops fitting it becomes a panel that closes on
  navigation. Curated rather than complete: most of `docs/` is internal design
  record. Cross-references to
  documents outside the published set render as their own label instead of as
  links that 404.
- **Audio, video and documents too large to send inline can now go through the
  provider's file upload instead of being refused.** Off by default and opt-in
  per org (`large_media`), because it exists to let much more expensive inputs
  through: the ceiling moves from about 18 MB to whatever the deployment allows,
  256 MB unless raised. **There are two ceilings and only one of them moves** —
  a file upload changes how much can be *sent*, not how much the model can
  *understand*, which is a duration for video and audio and a page count for a
  document. Something past the second is refused before anything is uploaded,
  with a sentence that says so rather than pointing at a setting that would not
  help. The transcript that comes back is chunked, embedded and retrievable like
  any other record.
  **No knowledge graph is built from these transcripts unless asked**
  (`large_media_graph`): an hour of speech-to-text yields hundreds of candidate
  entities, mostly mishearings, and every other record in the project resolves
  against the same entity table. The summary and the artifact are still written.
  New env vars: `LARGE_MEDIA`, `MAX_LARGE_MEDIA_BYTES`.
- **The console says where the project has got to.** Five stages above every
  screen — make a place, get something in, watch it climb, get it back, keep it
  — read from counts that already existed. Nearly every confusion this week was
  one confusion: a record was somewhere in the staircase and no screen said
  where, so "not findable" and "not enriched yet" looked identical.
  **It never calls `stored` broken** — enrichment is opt-in, so resting there is
  correct for most corpora, and a rail that is wrong more often than right is
  one people learn to skip. It links rather than offering a button that spends:
  one click in permanent chrome firing several hundred model calls is a footgun
  wherever it sits. It goes quiet when every stage is healthy. **Memories** is
  now its own group at the top of the sidebar, having sat under *Organize* —
  which is where you file something you already have, not where you begin.
- **A memory type can have its URLs read by the model rather than downloaded.**
  `url_reader = 'context'` asks Gemini to read the page and keeps an ordinary
  GET underneath as the backup. It exists because **a JavaScript-rendered page
  answers 200 with an empty shell**: the download succeeds, the old fallback
  never fired, and the record landed `stored` with no text and nothing reporting
  a problem. Per type, because the trade runs both ways — a GET returns bytes
  that are versioned, re-parseable and quotable. What is stored says which order
  produced it, and never claims the bytes were unavailable when none were asked
  for.
- **Point at a researcher's Scholar profile and get their papers.**
  `POST /api/v1/crawlers/from-scholar` reads the profile, resolves it to an
  OpenAlex author, and builds a crawler for their works: open-access papers
  arrive as downloaded PDFs, the rest as title, abstract and metadata with the
  record saying which. **The resolution refuses rather than guessing** — two
  researchers share a name, and the wrong author id gives a corpus that is
  entirely coherent and about somebody else, with every record real and nothing
  downstream able to detect it. A match is confirmed against papers the profile
  listed and needs two of them, since one shared title is a co-authorship.
- **A crawler listing can name a file to download**, not just a page about one.
  `Extract.pending_path` turns a link into a `Pending` reference the fetch
  worker resolves, so the byte cap, SSRF validation and parse pipeline are the
  ones already in use. `Extract.content_format: "inverted_index"` reconstructs
  an abstract from `{"word": [positions]}`, which several scholarly APIs ship
  in place of text they may index but not redistribute.
- **A memory type can enrich what lands in it.** `memory_types.enrich`, off by
  default. Per-write opt-in is the right grain for an inbox and the wrong one
  for a corpus that exists to be searched, where every record resting at
  `stored` is not a saving but the feature not working. **Deliberately separate
  from `url_reader`** — a memory of papers wants enrichment *and* wants its PDFs
  downloaded, and while the two were one rule, asking for the first asked for
  the second.
- **A memory that answers "what changed since last time".** A memory type can be
  marked as a **checkpoint timeline**: every record added to one of its memories
  becomes a checkpoint, is described on its own, and is compared with the one
  before it. A corpus fed the same document repeatedly — a nightly export, a
  weekly status report, a vendor feed — used to accumulate copies and answer
  from all of them at once, and the question people actually have about it had
  no way to be asked. `GET /api/v1/memories/{id}/checkpoints` reads the
  timeline; a **Timeline** panel on the memory renders it, and each type can be
  switched on from the console with the cost stated beside the switch.

  Three things keep it from becoming a noise generator, because a change
  detector that always finds something looks exactly like one that works.
  **A record identical to the one before it costs no model call at all** and
  reuses its predecessor's description. **Two descriptions written by different
  generator versions are marked `incomparable` rather than diffed**, since a
  prompt change would otherwise be reported as a content change in the shape a
  real finding has. And the description is a fixed-order list of observations
  rather than a paragraph, so two runs over the same content differ in content
  rather than in phrasing.

  **`unchanged` is stored, never inferred from an absence.** "The check has not
  finished", "it failed" and "it ran and found nothing" all produce a row with
  no changes on it, so `status` and `outcome` are separate throughout — for a
  change detector, rendering those alike turns silence into a clean bill of
  health.

  Records in a timeline are **not enriched and not added to the graph**, which
  is already the default: the check reads text and needs neither embeddings nor
  entities. **`POST /api/v1/memories/{id}/enrich`** is how that is reversed
  later, for every member at once — per-item forcing already existed, and doing
  it fifty times is the reason nobody did it at all.
- **Alerts can watch a timeline.** `checkpoint.changed` is a surface, so "tell
  me when the vendor feed changes materially" is a selector rather than a second
  feature.
- **Open an item in Browse and see where it got to.** Add data watched a write
  climb the staircase and threw the reading away the moment you navigated off
  the page, so "what happened to the thing I added" had no answer anywhere. The
  climb is now rebuilt from the record and its event log, so it works an hour
  later, in another session, and for an item somebody else wrote. The events
  are what make it worth having: `searchable` is the same row whether the
  summary is queued, refused, or was never asked for, and enrichment is opt-in,
  so a write that did not request it stops at `stored` permanently — correct
  behaviour that reads as a broken screen.
- **A report can ask for findings instead of a summary.** A generator may name
  the shape it needs: `data_type: code_review` puts a `findings` array in the
  envelope — file, symbol, severity, statement, trigger — with `file` required,
  because a finding that cannot say where it is cannot be checked. **An empty
  array is a first-class answer** and the honest one for code with no locatable
  defect: `[]` and a missing key are different claims, and rendering them the
  same is how a report that never ran reads as a clean bill of health. The bug
  report described the codebase instead of answering it four runs running, and
  no wording fixed it, because the envelope's one open field is `summary` and a
  summariser told to find bugs still writes a summary.
- **A page behind a bot wall can be read by the model.** When a fetch fails —
  403, a bot wall, a shell that fills itself in with JavaScript — Gemini's URL
  Context retrieves the page instead, and what was a stored record with no text
  becomes a readable one. It is a fallback and the order is the design: a GET
  returns the bytes somebody published, and this returns a model's reading of
  them. **The retrieval status is the whole safety property** — asked about a
  URL it could not reach, the model answers anyway, from training, and the
  prose is indistinguishable from a real reading. An account is accepted only
  when the metadata confirms retrieval; a response with no metadata is refused,
  because silence is not success.
- **Paste a GitHub URL and get four reports about that exact commit** —
  design, code quality, functional bugs, dependencies. Each is an artifact from
  a named generator, so it records the prompt and model that produced it, goes
  stale when either changes, and erases with its sources.

  **It is per snapshot, and the constraint is in the schema.** `(project, url,
  sha)` is UNIQUE, so asking twice for one commit returns the first snapshot
  rather than paying for a second clone and four more model calls. A branch is
  resolved once and the sha is stored — a report attributed to `main` is one
  nobody can reproduce later.

  **The code graph is the compression.** A repository exceeds every ceiling in
  [`docs/limit.md`](docs/limit.md) by orders of magnitude, so it is reduced
  first by graphify — tree-sitter, deterministic, no model — and the analysers
  read that plus a bounded file set: manifests, entry points and the most
  connected modules, capped at 40. Which files were read, and why each was
  chosen, is stored per snapshot and rendered on the screen: a report over
  twelve files and one over forty are different claims, and a thin report with
  no file count reads as a clean bill of health.

  **Vulnerability facts come from OSV, never from the model.** Asked whether a
  version is affected, a model produces fluent, plausible, wrong CVE numbers,
  and a wrong advisory is a security claim about somebody's software. The job
  queries OSV and the dependency prompt forbids reporting any advisory absent
  from that result. An unreachable OSV records `unavailable` rather than an
  empty result, and the console says *unchecked* — "no advisories" and "nobody
  asked" must not render the same.

  Cloning and parsing run as a **Cloud Run Job**, not in the API: it autoscales
  on request rate, so minutes of CPU count the same as a 20 ms write and starve
  the pool without triggering a scale-up. The job holds no database credential
  and writes back through the ordinary public write path.

- **[`docs/usage/local.md`](docs/usage/local.md) — running *and using* the whole
  stack on one machine**, with no cloud account, no model key and no billing.
  Records the trap that costs the time: `EMBED_DIM` must match the index in
  *every* process, so one set for `uvicorn` and forgotten for
  `python -m memdog seed` fires the dimension guard inside the seed and reads as
  a broken seed rather than a mismatched environment. Also that a local `/ask`
  quotes the closest passages instead of writing prose — still grounded, still
  cited, because both are properties of retrieval rather than of the model.
- **`docs/limit.md` — every ceiling on the way in, in one place.** Admission
  caps that return 413 (500 items, 32 MiB inline, 512 MiB upload, crawler
  budgets) separated from processing ceilings that store the bytes whole and
  index less than all of them, plus a per-kind breakdown for documents, images,
  audio and video — which ceiling binds depends on the kind, and it is rarely
  the one people assume.

  **Three things the code said and the docs did not.** `MAX_MEDIA_BYTES` is
  defined in `config.py` and never read, so setting it does nothing and the
  real ceiling is the hardcoded `MAX_INLINE_BYTES`. The 8,192-token *output*
  cap — not the 18 MiB input cap — is the binding limit on media: audio small
  enough to send still outruns its transcript at ~40–50 minutes of speech, and
  a scanned PDF gets roughly its first eighth OCR'd. And that cap is the only
  ceiling in the system that does not announce itself — `finishReason` is never
  inspected, so a transcript cut off at `MAX_TOKENS` lands looking complete.
  `structure.output_tokens` sitting at 8,192 is the only tell.
- **Paste a web-page URL into Add data, and the page is judged as well as
  stored.** `provider: "url"` had been wired, SSRF-hardened and tested since
  the beginning, and nothing had ever produced one. `document_html` now gets
  its own extraction prompt, which fills a `quality` block on the artifact:
  what kind of page it is, what it wants from the reader, whether anything is
  sourced, who wrote it, when, how it is monetised, up to five specific reasons
  to trust or doubt it, what it leaves unanswered, and whether it is worth
  keeping at all. The values are enumerated rather than prose, so a corpus can
  be filtered on them — `not_content` exists because an error page, a login
  wall and a parked domain all arrive as HTTP 200 with fluent text, and one
  stored as an article looks exactly like an article that summarised badly.
  The block sits behind `entities` and `relations` and ahead of `summary`, and
  every field is length-bounded in the skeleton where no per-type override can
  raise it. The console renders it as sentences and chips on the record.
- **Paste a YouTube URL into Add data.** The URL is written as a `Pending` ref
  with `provider: "youtube"`; everything after the fetch — classification,
  parsing, embedding, enrichment, entity resolution and edges — runs unchanged
  and never learns a video was involved.

  **What is stored is an account of the video, not a transcript.** Both
  transcript routes are closed: YouTube's `timedtext` endpoint now answers a
  bare request with 200 and zero bytes, and the official Data API hands
  captions only to the account owning the video. Downloading and transcribing
  hits the 18 MB inline ceiling — a minute of 720p exceeds it — and would need
  ffmpeg in the runtime image, which `pyproject.toml` rules out. Gemini instead
  takes the URL directly and watches the video, and asked for a verbatim
  transcript it stops with `finishReason: RECITATION` and returns nothing.
  Asked for a structured account — section by section with timestamps, terms
  and people introduced, how they relate, short attributed quotes — it returns
  what the graph actually needs. Measured on a 19-minute talk: 8,319 bytes,
  10 entities, 5 edges, and questions about it answered with citations.

  Needs `MEDIA_INTERPRETATION=true` and `GEMINI_API_KEY`; without them a
  YouTube reference is refused as a configuration error rather than silently
  doing nothing. Costs roughly **100,000 input tokens per 20 minutes** of
  video, since the model reads frames as well as audio — stated next to the
  field that spends it.
- **The public demo's transcript can be cleared.** The console's chat has had a
  Clear since it was built and the demo shipped without one, so a visitor done
  with a conversation had no way back to an empty card — or to the starter
  questions, which only render when there are no turns.
- **Anyone can question a corpus here without an account.** The landing page is
  now a chat over Sir Edwin Arnold's *The Song Celestial* (1885, public domain)
  — eighteen chapters ingested exactly as your own documents would be. Every
  answer cites the passage behind it, and when the text does not support one it
  says so instead of composing it. `GET /api/v1/public/demo` reports whether the
  demo is on and how much of the day's budget is left; `POST /api/v1/public/ask`
  answers one question. The project and memory are named in **configuration,
  never in the request**, so there is no scope for a caller to widen, and the
  principal is synthetic with `DATA_READ` alone and a `user_id` matching no real
  user — private records stay as invisible to it as they are to a stranger.
  Metered before the model call, not after: an endpoint that counts afterwards
  gives every failing request a free one. Off unless `PUBLIC_PROJECT_ID` is set.
- **The overview answers "can a vector search find this?".** Every number on
  that screen could be right — all records enriched, every chunk embedded, one
  vector space, the configured model and dimension — while vector search matched
  nothing at all, and nothing anywhere disagreed. It now reports which vector
  spaces the records are in, which one retrieval queries, how many records are
  stranded outside it, and whether the index still returns a full set of
  neighbours for a vector it already holds.
- **OpenClaw runs against the same corpus as a Cloud Run Job** (`agents/openclaw/`),
  and it needed no code to do it — mem-dog's MCP speaks `Authorization: Bearer`
  and OpenClaw sends exactly that, so the integration is one `mcp.servers` entry.
  It read and correctly attributed the record the Hermes agent had written about
  its own investigation: two vendors' agent runtimes sharing memory through
  mem-dog, neither aware of the other. A **Job rather than a service** because
  Cloud Run injects `X-Forwarded-*`, which routes calls onto OpenClaw's
  trusted-proxy path — and its `/tools/invoke` is a full operator-access surface
  upstream says must never be public.
- **A repository can be removed.** `DELETE /api/v1/repos/{case_id}` stops
  tracking one and takes every snapshot of it, with a Remove control on each row
  of the console's repository list. Analysis is the only thing in the console
  that creates a container from a button, and it was the only one with no way
  back — a repository added by mistake stayed on the list for good, and deleting
  its snapshots one at a time left the row behind claiming a history that was
  gone. Snapshots go out through the ordinary memory deletion, so they get the
  same tombstone, blob reclamation and audit trail as anything else. The
  confirmation carries the count, because "remove with fourteen snapshots?" is
  not the same question as one with none; so does the result, since "Removed"
  alone cannot be told apart from a delete that reached the row and none of its
  snapshots. **A snapshot that could not be deleted is named, with its reason**,
  rather than folded into a number.

### Configuration
- **`URL_CONTEXT`** — reads a page with Gemini's URL Context when the fetcher
  cannot get it. **Off by default**: a page that fetches normally costs an HTTP
  GET, and this costs a model call whose input includes the whole page. It
  earns that only where the alternative is a record with no text.
  **`URL_CONTEXT_MODEL`** overrides the model; empty means `MULTIMODAL_MODEL`.
- **`REPO_ANALYSIS_JOB`** — the fully qualified Cloud Run Job
  (`projects/{p}/locations/{l}/jobs/{name}`) that clones and graphs a
  repository. **Empty disables repository analysis, which is the default**: the
  job clones arbitrary public repositories and spends four model calls per
  snapshot, so it is switched on deliberately rather than inherited. A snapshot
  requested without it is recorded and immediately marked failed with that as
  its reason — never left pending, which would read as still running.

### Changed
- **The copyright notice is on the sign-in card**, not only in the page footer —
  when a demo is present the card is a popover off the header, so the footer is
  nowhere near the person signing in. The landing footer is now the notice
  alone; the "every claim on this page" line is gone.
- **"How it compares" moved from the landing header into the tab strip**, beside
  the demo and the diagrams — it is one of the things a visitor came to do, not
  a utility link. It stays an anchor rather than becoming a tab, with an arrow
  marking that it leaves the page, because a link wearing `role="tab"` promises
  a panel that switches in place. The strip itself now renders unconditionally
  so a deployment without a published demo gallery cannot lose the link.
- **The landing page's diagrams are sub-tabs rather than a column.** Four
  stacked was a long scroll with no indication of how much was left, so the last
  one may as well not have existed. The system diagram, the company brain, the
  timeline memory and agent memory are now a pill row, one screen each. The
  system diagram gave up its own section heading to become a pane like the
  others, and its `#flow` anchor went with it — nothing linked it.
- **A repository is added from Add data now, not from its own screen.** Two nav
  entries beginning with "Add" were two answers to "where do I put something",
  and a repository is not a different action — it is a different kind of thing.
  It joins text, files, YouTube videos and web pages as a fifth kind in step 1.
  Steps 3 to 5 are *absent* rather than greyed out for it, because a repository
  goes to `repos/analyze` and produces a snapshot rather than an item, so none
  of the memory, enrichment or audience decisions apply; the line above the
  button says what stands in their place, and the cost sits next to the control
  that causes it. The confirmation offers a button through to the snapshot
  instead of announcing that the job is somewhere else. The old screen keeps its
  own entry as **Repositories** — the reading half — with its form removed.
- **A checkpoint's status and the event announcing it are written together.**
  Both `checkpoint.changed` and `checkpoint.checked` are emitted inside the
  transaction that settles the row, so a checkpoint cannot be complete in the
  table and absent from the stream. What the events report is what was stored,
  not what the caller asked for — the outcome column coalesces, so a failed
  check on a first checkpoint keeps `first` and the event says `first` too.
- **A `checkpoint.changed` event carries the delta, not just its size.**
  Alongside the count: per-kind counts, the highest significance in the batch, a
  five-item sample of the change statements, and the `change_artifact_id` that
  reaches the whole comparison. A push delivery is the event payload and nothing
  else, so a subscriber that saw only `changes: 5` had to make a second
  authenticated call to find out what moved. The new fields are scalars and one
  short list on purpose — a selector's `in` operator cannot reach into a nested
  object, so a tidier shape would have been declared, emitted and still
  unusable. `{"highest_significance": ["high"]}` and
  `{"statements": {"op": "contains", "value": ["price"]}}` are now selectors.
  The console describes these events as a sentence rather than printing every
  key, which is what it did before the payload grew.
- **The landing page is one diagram and the demo, not nine sections of prose.**
  The receipt, the pipeline figure, the nine explanatory sections and the
  six-product comparison matrix are gone — all of it is in the docs, which are
  now one click away in the header, so the argument was being made twice and
  finished once. What replaces it is a single picture of the whole system, in
  five named stages: **pull** and **push** sources into one write path, which
  builds a retrieval index, a knowledge graph and a reverse index — together the
  memory — reached through chat, MCP or the API, and carrying a company brain,
  agent memory, alerts, pattern search and workflows. The page went from roughly
  14,000 pixels of scroll to 1,339.
- **Connecting an app no longer asks you to supply what the catalog already
  knows.** Every connector entry carries the auth style it wants and, for 23 of
  its 45 scopes, help explaining the one field only the operator can fill —
  and none of that help reached the screen. For Jira it is the clause that
  decides whether a scheduled pull is incremental: without it every run re-reads
  the project, and sorted descending the watermark advances past issues nobody
  read. It is rendered under the field now. **"Register one"** beside the
  credential picker fills the form below with the app's provider key, auth style
  and header name — choosing Jira had been followed by scrolling down, typing
  `jira` into a free-text box and remembering to switch from bearer to basic —
  and the connection it creates is selected back into the app above. Picking an
  app with exactly one credential already registered for it selects that one.
  Creating a crawler with no credential now says what will happen rather than
  letting the dry run come back unauthorised unexplained.
- **The console is one story with the rest arranged behind it.** The sidebar had
  nine headings and twenty-six destinations, all at the same volume, and the
  four screens the product exists for were spread across four of them.
  `Your data` now holds the whole path — Memories, Add data, Add a repo, Deep
  dive, Chat — and everything else is a supporting heading below it. Seven
  groups, twenty-two destinations. **Browse is Deep dive**: after the drill-down
  it is mostly reading, and a label promising an edit made people who wanted to
  look skip it.
- **Search is folded into Chat.** It was never a rival way to ask a question; it
  was the explanation of an answer, and standing beside Chat as a second box
  with a second query meant nothing could tell you whether what it retrieved was
  what the answer had read. **How it found this**, under any answer, now opens
  the cited passages and then the retrieval itself — the same question with the
  same filter, arms changeable so "would the graph arm have found it?" can
  actually be asked, what was retrieved and ranked, what was considered and
  dropped and why, and which model embedded it. Re-running a retrieval never
  re-asks the model.
- **Creating a memory asks for a name.** The memory *type* was the first control
  on the screen, in front of the field everybody came to fill in, and it is
  almost never a first decision — the policies ship configured. New memories are
  now `default`: everything kept, nothing expires. The old default was
  `session`, which quietly archived after a day. The policy is folded under the
  create form and always says what it currently is, so it is never a silent
  default, and the full editable table — TTL, expiry, enrichment, URL reading,
  change tracking — sits collapsed at the foot of the screen with its
  explanation in the help panel. The type is no longer stamped on every row of
  the memory list when it is the ordinary one.
- **`api/uv.lock` is tracked.** There is no CI in this repo, so `pytest` on a
  laptop is the only gate there is, and a gate that resolves a different
  dependency set each run is not much of one. Nothing about a deploy changes:
  `api/Dockerfile` builds with `pip` from `pyproject.toml` and never copies the
  lockfile into the image.
- **The README and the console's sign-in page are an elevator pitch rather than
  a specification.** Three hundred lines of feature inventory that nobody reads
  to the end of said less about what this is than four lines do.

### Migrations
- **`0058_checkpoint_deferred.sql`** — widens the checkpoint status constraint
  with `deferred` and replaces the pending index to cover it. Applies itself on
  startup; without it a postponed check would violate the CHECK constraint.
- **`0057_memory_change_ranges.sql`** — the stored net answer for a span of a
  checkpoint timeline. Applies itself on the first startup of the new revision,
  as every migration here does; nothing but `?net=true` reads the table, so the
  composed range and both event surfaces do not depend on it.
- **`0055_memory_type_url_reader.sql`** — `memory_types.url_reader`
  (`fetch` | `context`). Additive, defaulting to `fetch`, which is exactly
  today's behaviour, so every existing type is unchanged by definition.
- **`0056_memory_type_enrich.sql`** — `memory_types.enrich`. Additive and off by
  default, so nothing starts spending because this shipped.
- **`0053_memory_checkpoints.sql`** — `memory_checkpoints`, and a `checkpoints`
  boolean on `memory_types`. Additive, and **off by default**: turning it on
  puts up to two model calls behind every record written into any memory of that
  type, so it is switched on deliberately rather than inherited.
- **`0054_artifact_fields_are_objects.sql`** — repairs artifacts whose `fields`
  was stored as a JSON *string* rather than a JSON object. Data repair rather
  than schema: it rewrites only rows where `jsonb_typeof(fields) = 'string'` and
  the text really is an object, and is a no-op on every correctly written one.
- **`0052_repo_memory_titles.sql`** — gives repository memories created before
  titles existed a title, derived from `owner/repo@sha` rather than guessed. A
  memory created implicitly by a write carries no title, so half the repository
  entries in every picker were forty characters of hex — which is what made the
  useful ones impossible to find among them. A key not matching that shape is
  left alone: a wrong title is worse than none, because none at least falls back
  to something true. A migration rather than a script, because a script is
  something somebody has to remember to run against a database on a private
  address.
- **`0051_repo_snapshots.sql`** — `repo_snapshots`. Additive. The UNIQUE on
  `(project_id, repo_url, commit_sha)` is the per-snapshot rule itself, and a
  CHECK requires a full 40-character lowercase sha so one commit cannot be
  analysed twice under two spellings.

### Removed
- **Cases, Entities, Workflows and the Producers screen are gone from the
  console.** Four screens describing how a corpus is arranged or how a source is
  doing, none of them on the path from *I have data* to *I have an answer*. The
  endpoints behind them are untouched and still serve the API and the MCP tools;
  what left is the navigation, the screens, and the proxy allow-list entries
  only those screens used. An alert can no longer be scoped to a case, and the
  Overview no longer counts them.

### Removed
- `GET /api/v1/public/demo` — superseded by `GET /api/v1/public/demos`, which
  returns everything it did and the rest of the gallery besides. Two endpoints
  answering overlapping questions about one thing is how they drift apart.

### Fixed
- **A rate-limited checkpoint said `failed` about work that was coming back.**
  The queue classifies a capacity error as deferred and retries it, but the row
  was written `failed` first — harmless while only the console read it, and then
  `checkpoint.checked` put it on the event stream where it announced a failure
  for something that succeeds on the next attempt. `deferred` is now its own
  state, classified by the same function the queue uses so the two cannot
  disagree, swept like `pending`, and reported in a range as not-yet-checked
  rather than as a failure.
- **The landing page painted its hero and every diagram, then discarded them.**
  The demo gallery loads client-side and `null` meant both "not asked yet" and
  "there is none", so the first paint took the no-demo branch and the whole
  screen was replaced the moment the fetch landed. The flash predates the
  diagrams — it used to swap a small hero — but the diagrams made it a full
  screen. Unresolved and none are now separate states, and the demo shell
  renders optimistically while the gallery is in flight.
- **Every deploy was silently wiping the public demo gallery.** `PUBLIC_DEMOS`
  is JSON and therefore full of commas — the character `--set-env-vars` splits
  on — so it was never in the deploy script's list, which meant it lived only on
  the running service and `--set-env-vars` replaces the whole set. Nothing errors
  when that happens: the landing page just has no gallery, which reads as a UI
  regression rather than a deploy that lost a variable. `cloudrun.sh` now passes
  the environment through `--env-vars-file` (JSON is valid YAML, so there is no
  delimiter to collide with) and defaults `PUBLIC_DEMOS` to whatever the service
  already has, since it is data `seed-demos` produced rather than a toggle with a
  sensible constant. Pass `PUBLIC_DEMOS=''` to clear it deliberately.
- **An alert on a checkpoint timeline's memory type could never fire.** The
  `checkpoint.changed` surface has declared `memory_type` selectable since it
  shipped, and the transition never carried it — so "tell me when the vendor
  feed changes" was accepted, backtested green against nothing, enabled, and
  then matched nothing forever. There is no way for that to raise: a missing
  field reads as `None`, every operator is decidable against `None`, and an
  alert that never fires is indistinguishable from a feed that never moved.
  The payload names the memory type now, and a test compares the declared
  fields against the emitted ones so the two cannot drift apart again.
- **The change generator's prompt constrained two fields that no longer
  existed.** It still instructed the model about `before` and `after` after the
  schema renamed them to `earlier_value` and `later_value` — the rename that
  took this generator from 7,944 output tokens to 534 — so the length-and-
  purpose guard read as present and applied to nothing.
- **A seeded demo corpus answered every question for the seeder and nothing for
  anyone else.** ACL inheritance follows the connection, and the gallery seeder
  created its producer without one — so every record landed `private`, invisible
  to the public visitor whose `user_id` matches no real user by design. From
  outside it looked like "not supported by the text" on every question, which is
  what an empty corpus looks like and is indistinguishable from a bad one.
  Records are written through the org's shared connection now, a missing shared
  connection is a refusal rather than a default, and the seed checks that every
  record is org-visible before publishing — so "the questions pass" can no
  longer mean "the questions pass for me". The seeder also picked the *oldest*
  organization, the same trap the deploy runbook records for `add-member`; it
  chooses the org that has a shared connection, or takes one as an argument.
- **Recording a video failed, and the console said `[object Object]`.** Two
  faults, and the second hid the first. A data URL's header is stripped by
  slicing at the first comma — but `MediaRecorder` emits
  `data:video/webm;codecs=vp8,opus;base64,…`, so the slice landed inside the
  codec list and the payload was sent as `opus;base64,GkXfo59…`. The API
  refused it correctly. Audio (`codecs=opus`), photos and chosen files carry no
  comma, which is why only video broke. Separately, `detail` on a refusal is a
  *list of objects* when FastAPI's own validation rejects a request, and
  `new Error(thatList)` stringifies to `[object Object]` — so the server named
  the exact field and the exact reason, and none of it reached the screen.
  Refusals now read as a sentence, and the encoder anchors on `;base64,`, which
  cannot appear inside a payload.
- **A crawl that stopped at the page cap reported itself as complete, and the
  watermark moved past everything it never read.** `max_items` and the wall
  clock both come back from a run as a stop reason, which marks it `partial` —
  and partial is what holds the watermark still. `max_pages` was the one limit
  wired to nothing: the loop ended, no reason came back, and the run was filed
  as complete. Any source deeper than `max_pages × page_size` lost the
  remainder permanently, with a plausible count and no error anywhere. The
  same silence covered folders below `max_depth` in a Drive or SharePoint walk.
  Both say so now, and the run is `partial` with the bound named.
- **Four Microsoft Graph connectors read one page and stopped.** Outlook,
  Teams, SharePoint and OneDrive declared no pagination, while Dynamics — the
  same API — followed `@odata.nextLink` correctly: 2 of 5 records against the
  simulator, then a watermark stored as though all five had been read. What let
  it last is that the *mechanism* was tested and the entries that needed it were
  not.
- **Attio pulled the first 100 records of an object and said nothing about it.**
  It pages from a POST body the query-string pager cannot reach — the same
  limitation Linear and Copper carry, and those two say so where the console
  shows it. An entry that cannot page now has to admit it in `notes`, which is
  the part a person configuring the app actually reads.
- **A fetch worker built without settings raised instead of switching video
  reading off.** `build_video_reader` read the flag directly where
  `build_url_reader`, two lines away in the same constructor, read it
  defensively.
- **A site URL the crawler would refuse to fetch came back as a 500.**
  `validate_url` raises `FetchError`, which no endpoint caught and pydantic does
  not convert, so a private, unresolvable or non-http URL produced an HTML error
  page — and the console, parsing it as JSON, reported `Unexpected token 'I'`.
  The SSRF guard was right and unreadable. It is a 422 carrying the reason now,
  on every path that stores a crawler, including `from-connector` where the site
  URL is the one thing the operator supplies.
- **Registering a credential, or creating a crawler from an app, left the screen
  showing the state before it.** Neither reloaded, so a credential just
  registered appeared in no dropdown and "run a dry run next" pointed at a row
  that was not on the page.
- **A generator spent its whole output budget repeating one word.** An unnamed,
  unbounded string field became somewhere for the model to think: 7,944 tokens,
  `MAX_TOKENS`, 31KB of truncated JSON, arriving as an empty extraction failure
  that named neither the model nor the budget. Naming the fields for the labels
  the prompt already used, and requiring them, took it to 534 tokens and from
  one change found to five.
- **A model's prose was being parsed for facts.** Asked for an account of a
  profile the model returned markdown, and the parser looking for `Name:` lines
  took `### Overview` for a researcher's name. Not a regex to improve: URL
  Context accepts a response schema, so it asks for the shape now.
- **An author's affiliation silently vanished.** OpenAlex renamed
  `last_known_institution` to the plural; the old key is still present as null,
  so reading it returned None rather than raising, and everything else about the
  resolution looked correct.
- **A crawler default was travelling as a decision.** `CrawlerConfig.enrich` was
  `False` and passed explicitly on every write, so the rule that protects a
  stated "no" overrode a memory type asking for enrichment — a hundred papers
  pulled into a corpus that exists to be searched arrived entirely at `stored`.
  It is three-valued now (`None` = "the crawler did not say"), like
  `WriteOptions.enrich`, whose comment documents exactly this distinction.
- **A structured artifact stored its structure as text.** The connection pool
  sets a jsonb codec that already encodes with `json.dumps`, and the artifact
  insert called `json.dumps` as well — so `fields` was written as a jsonb string
  containing JSON. Nothing errored: the artifact existed, its title and summary
  rendered normally, and only the structured half came back as text, so every
  consumer reading `fields.findings` got nothing. **Repository review findings
  have been unreadable since they shipped, for this reason.** Fixed at the
  writer, with `0054` repairing what was already stored.
- **Running out of output budget now says so.** `docs/limit.md` named this as
  the ceiling that does not announce itself: the 8,192-token cap is enforced by
  the provider and `finishReason` was never inspected, so a full budget arrived
  as an empty extraction failure, or as a parse error on a truncated string —
  both of which read as a broken model rather than a full one. It took a live
  deploy to see, on a generator that ran to 7,944 tokens repeating one word.
- **One memory per repository snapshot, instead of two.** Every analysis wrote a
  second, near-identical memory holding the raw code graph, so the memory list
  read as duplicated and picking the right one of a pair was guesswork. The
  graph now lives with the snapshot it belongs to, tagged so reports skip it —
  visible where it should be, and still never read into a report as input.
- **A repository's analysis can be chatted with.** The job wrote its records
  with no `options`, so enrichment resolved off and the reports landed `stored`
  rather than `searchable`: six of thirty-six records were findable, and asking
  about a repository returned nothing. Nothing errored, because a write that
  does not request enrichment stopping at `stored` is correct behaviour — which
  is exactly why it took counting the rows to see.
- **A failed repository snapshot can be retried.** Re-use returned the
  existing row whatever its status, so the unique key on
  `(project, url, sha)` made a failure permanent for that commit: a snapshot
  that failed because the deployment had no `REPO_ANALYSIS_JOB` configured
  stayed failed after the job was configured, asking again handed back the same
  dead row, and the only escape was analysing a different commit. Re-use exists
  to avoid paying twice for work that succeeded; work that failed was never
  paid for. A failed snapshot now re-enqueues and its stale reason is cleared,
  while a completed one is still returned as-is.
- **A document larger than a JSON body could not be added at all.** The console
  had exactly one way to send a file — base64 inline — and the three upload
  endpoints were missing from the proxy allow-list, so a book-sized PDF had no
  route in and failed before any request was made. Audio, images and short
  recordings fit and worked, which is why this read as "PDFs are broken" rather
  than as a missing path. Four things had to change: the proxy dropped
  `X-Upload-Token` (the token *is* the capability, so the API answered 403,
  which reads as a permissions bug and is a dropped header); the proxy read the
  body as text, which corrupts a PDF; `POST /uploads/{id}/complete` accepted
  only `external_id` and `memory`, so the upload path silently dropped the ACL,
  the template and the request to enrich; and staging encoded to base64 the
  instant a file was chosen, before anyone had decided to write it.
- **A file erased once could never be added again.** The write upsert's
  conflict target is `(project, producer, external_id)` and it matched
  tombstoned rows without clearing `deleted_at` — so re-adding a file after an
  erasure updated the dead row and returned its id. The response said success,
  every read of that id answered 404 because the ACL predicate excludes deleted
  rows, and the item was never saved: written into a row nothing could see,
  with no error anywhere. A write onto an erased row now clears the tombstone.
  The erasure itself is not rewritten — the audit event and the certificate
  still say it was erased, and when.
- **Audio was recorded as silent when it was not.** A recording of somebody
  speaking came back empty from the configured `TRANSCRIBE_MODEL` and was
  stored as "no interpretable content found in the media" — a true-sounding
  sentence about something untrue. The same bytes through `MULTIMODAL_MODEL`
  transcribed correctly. It is the quiet mirror of a failure already recorded
  for video, which fails loudly against the same model and was routed away.
  An empty answer from an overridden model is now retried once against the
  engine's own model, and only silence from *that* is recorded as silence.
- **The console reported successful writes as failures.** `207` is
  Multi-Status, not success — it can carry an item that `failed` with the
  reason in `error` — and the console read `results[0].data_id` regardless, so
  a refusal raised "cannot read properties of undefined" and the API's own
  explanation went on the floor. Separately, a 404 while watching an item was
  fatal rather than a state: deleting a corpus while the console still held a
  tracked id was enough to make every later write look broken. Both are the
  same mistake — treating "I could not read the answer" as "the operation
  failed" — and between them they hid every real cause behind a type error.
- **A repository graphify finds no code in is a result, not a crash.** It
  refuses to write `graph.json` when extraction produces no nodes, which is
  correct for a docs-only repo, a notebook corpus, or a language with no
  grammar. Failing the snapshot there threw away the manifests and the README,
  which need no graph and are most of the dependency and design material.
- **The Hermes agent reported the mem-dog corpus as empty.** `mem_dog_search`
  requires a `project_id`, nothing in the MCP handshake supplies one, and the
  agent had been discovering it by shelling out to `env | grep -i mem` — so it
  only ever worked because the terminal tool was open, and closing that hole
  turned every search into a silent miss. The ids are now seeded into `SOUL.md`
  at boot. Prompt tokens for the same question fell from 366,821 to 12,163,
  most of which was the agent hunting for an identifier it could have been
  handed.
- **A web page fetched from a URL was classified as plain text.** `filetype`
  knows containers and magic numbers, not markup, so a page guessed as nothing,
  decoded cleanly and came back `text/plain` — making `document_html`
  unreachable through the fetch path entirely, with every fetched page typed
  `document_text` and read with the document prompt. The HTML check existed all
  along, in the branch that only runs when there are no bytes. Both branches
  share one now, and it looks past a BOM, an XML declaration or a licence
  comment before giving up.
- **"Send a test delivery as the provider would" was signed the generic way for
  every provider.** The endpoint runs the real receive path precisely so a
  producer whose signing is broken fails the test — and then it hand-rolled
  `x-signature` over `{ts}.{body}` regardless of the preset. A producer on the
  Slack preset got a signature Slack's scheme never looks for, so the console's
  button answered 401 for a perfectly good secret; the same held for Zoom,
  Linear, Shopify, Twilio, Stripe and Graph. `providers.sign()` is now the
  mirror of `providers.verify()` and lives beside it, and `SignatureScheme`
  gains a `prefix` so a signature we send carries `v0=` / `sha256=` the way the
  provider's does. Checked across the whole registry.
- **An alert on a fact, scoped to a memory, matched nothing forever.**
  `fact.*` events carry no `data_id` — a fact is not a record — and the scope
  filter built its candidate set from that column alone, so any container scope
  (`memory_id`, `case_id`, `producer_id`) over a fact surface intersected with
  an empty set. No error, no empty state, and a rule that never fires looks
  exactly like a quiet week. A derived fact does stand on records, so the scope
  now resolves through its edges' evidence and the fact survives if any of that
  evidence is in the container. An asserted fact has none and still survives no
  container scope — `entity_id` is the scope that reaches it.
- **Vector search returned nothing, and every other number said it was fine.**
  Lexical search worked, so questions phrased in words the text uses literally
  were answered and the rest came back *"nothing matched"*. Two causes stacked.
  The HNSW graph had degraded: re-embedding deletes and reinserts every chunk of
  a record, and a corpus re-embedded a few times over while an extraction is
  being got right leaves enough dead tuples that a top-40 search for a vector
  *already in the table* returns 11 rows — eventually none. And the vector arm
  asked the index for exactly as many candidates as it wanted: `ef_search`
  defaults to 40, the arm over-fetches 40, and the ACL and filters are applied
  *after* the scan, so any filtering at all comes straight out of the result.
- **The public demo could not be used from a browser at all.** The proxy
  forwarded its body without a content-type — `apiFetch` sets one, but after the
  credential-free branch has already returned, and that is the branch an
  anonymous request takes. FastAPI parsed the JSON as a string and answered
  *"Input should be a valid dictionary"*, which the page rendered as
  `[object Object]`, because a validation `detail` is a list of objects and
  `new Error(list)` stringifies to exactly that. The proxy sends the type now,
  and the page turns whatever `detail` holds into a sentence.

### Changed
- **`document_html` resolves to a new prompt**, so existing HTML artifacts are
  detectably stale and will be re-derived by a reprocess. Nothing is lost; the
  old summaries stand until then.
- **The landing page leads with the corpus, not with a sign-in form.** The demo
  takes the full column at the top of the page and sign-in is a panel off the
  top bar: the first thing a visitor can do is ask a question, and a form
  demanding an account they do not have is the opposite of that. The headline,
  lede and counts follow underneath, as an explanation of something already
  seen rather than a claim to be taken on faith. The transcript scrolls inside
  its own card so the composer does not walk down the page with every answer.

### Migrations
- `0050_rebuild_vector_index.sql` — drops and recreates the HNSW index over
  `embeddings`. Run before deploying; it is a rebuild, so it takes time
  proportional to the corpus.
- `0049_public_asks.sql` — `public_asks`, the rate-limit and daily-cap ledger
  behind the public endpoint. Run before deploying.

### Configuration
- `PUBLIC_PROJECT_ID`, `PUBLIC_MEMORY_ID`, `PUBLIC_TITLE`, `PUBLIC_SUBTITLE`,
  `PUBLIC_DAILY_CAP` (500) and `PUBLIC_RATE_PER_HOUR` (20) configure the public
  endpoint. Without `PUBLIC_PROJECT_ID` it 404s and the landing page shows the
  sign-in card in the hero instead. Note that `--set-env-vars` splits on commas,
  so a subtitle containing one silently truncates the whole list.
- `HNSW_EF_SEARCH` (200) is how many candidates the vector arm's index scan
  visits before the ACL and filters cut it down. It must exceed what the arm
  over-fetches or filtering eats the result.

### Fixed
- **A book produced the graph of a single page, and then of nothing at all.**
  Three stacked defects, each hidden by the last:
  `text[:200_000]` silently discarded the tail of any longer document (a
  232,412-character Bhagavad Gita came back titled *"…Chapters 1 through 16"*);
  the windowing that fixed it had no effect, because `max_input_chars` was
  declared on the concrete extractors while the worker holds a
  `ChainedExtractor`, so `getattr` returned 0 and nothing split; and the window
  itself was sized by the model's **context limit** rather than by what it
  extracts well from — at 200,000 characters the model writes a summary and
  returns no entities and no keywords at all. `EXTRACT_WINDOW` (40,000,
  env-overridable) is now a separate number and the narrower of the two wins.
- **`entities` and `relations` were optional in the Gemini response schema.**
  Listing them first did nothing — Gemini does not emit in declaration order and
  an optional property may be absent entirely. A windowed extraction returned
  `{title, description, language, summary}`, ran out of output tokens
  mid-sentence, and the truncated JSON failed to parse, retried five times and
  was dropped. They are required now, with `propertyOrdering` putting the graph
  ahead of the summary so a runaway summary costs the summary and not the whole
  envelope.
- **`summary` had no length bound** and a model that starts rambling in it does
  not stop — one extraction produced thousands of words of run-on prose and lost
  the envelope. Bounded in the shared prompt, where no per-type override can
  drop it.

  Measured on the same book: **15 entities / 6 edges** before, **0** once
  windowing exposed the schema defect, **35 entities / 19 edges** after, read as
  six windows in under a minute.

### Added
- **Pick a memory in Chat and see what is in it.**
  `GET /api/v1/memories/{id}/context` returns record counts by state, the
  templates its records were read under, the keywords its artifacts carry, the
  entities its records name, and the relationships those records assert — all
  scoped by the caller's own visibility, so two people may legitimately see
  different totals for one memory. In the console the keywords and entities are
  controls, not decoration: clicking a keyword narrows the question, clicking an
  entity anchors the graph on it.
- **Extraction reads the whole document.** Embedding has always chunked;
  extraction never did, so a record's text was fully searchable while its
  understanding described only what fitted in one model call. A 232,412-character
  Bhagavad Gita came back titled *"Summary of Bhagavad Gita Chapters 1 through
  16"* with 15 entities for 18 chapters — `text[:200_000]` discarded 32,412
  characters and the artifact stored as a success. Documents are now split on
  paragraph boundaries and merged: narrative fields from the first window, the
  graph cumulative. Capped at `MAX_EXTRACT_WINDOWS` (default 12) because each
  window is a model call, and the remainder is reported on the artifact as
  `windows_skipped` rather than dropped silently.

### Fixed
- **Chat said "Nothing in the corpus matched that question" for three different
  situations.** After picking a lens or an anchor it read as *"your data does not
  say"* when the real cause was a scope that selected no records to search at
  all. It now distinguishes *no records are in scope* (naming the scope), *none
  is searchable yet*, and *nothing matched among the N searchable records*.

- **The Add data panel never said whether the graph was built.** The final step
  read *"title, summary, keywords and entities recorded"* — asserting entities
  on every successful enrichment and reporting no number, so a record that
  produced eighteen and one that produced none looked identical. There is now a
  **connected** step saying what was actually recorded (*"18 entities and 9
  relationships, read as scripture"*, or *"nothing to connect — no scripture
  relationships were found in this text"*), and the headline says *"Enriched —
  but nothing was named, so it is not in the graph"* when that is what happened.
  Entities with no edges is named as the ordinary case rather than a fault: a
  relationship has to be stated, and most text names things without asserting
  anything between them.

### Added
- `GET /api/v1/data/{id}` returns `entity_count`, `edge_count` and `template`,
  counted in the item's own query — a panel needing two calls to decide whether
  a step finished will eventually show one of them stale.

- **Chat can search the graph, anchored on what you point at.** The Chat scope
  picker had one control — memories — while retrieval already accepted tags,
  keywords and templates and could run a graph arm nobody could reach. It now
  offers *read as* (the template a record was written under, which narrows both
  the records searched and the relationships walked), *start from* (entities
  named outright), and *follow connections* (the graph arm). Answers report what
  the graph started from: **"Followed connections from Krishna (you chose it)"**
  — a result reached only through the graph does not contain the words searched
  for, so without the seed a reader cannot tell whether the connection was the
  one they meant.
- **`RetrieveFilter.entity_ids` anchors retrieval on named entities.** It keeps
  only records mentioning them *and* replaces the guesswork in `graph_seeds()`,
  which scrapes entity names out of the question text. Asked *"what is the chain
  that ends in ruin?"* the parser finds nothing to key off and the graph arm
  sits idle; anchored, it starts there and answers. A named anchor replaces the
  parsed one rather than adding to it — if the caller said where to start,
  starting elsewhere as well is not extra recall, it is their scope being
  quietly widened. `GraphSeed.matched_on` gains `chosen`, which is not a match
  but the caller insisting, kept distinct so a reader can tell an entity the
  system found from one a person named. Anchors are visibility-checked the way
  name resolution is: an id is easier to enumerate than a name, and passing one
  must not confirm an entity exists to somebody who can see no record naming it.

- **Templates decide the shape of the graph before the document is read.** A
  write may declare `template` per item — what the content is *for*, which the
  bytes cannot say. It narrows the relationships the model may report and adds
  an instruction block, so the same `.docx` read as `scripture` and read plainly
  produce different graphs. `GET /api/v1/templates` serves the three shipped
  (`scripture`, `design-doc`, `incident`) with the questions each exists to
  answer; an unknown name is refused with a 400 naming the valid ones, because
  ignoring a typo yields a generic graph the caller believes is specialised.
  `GET /api/v1/entities/{id}/graph` takes `template=` to walk only the edges one
  lens drew, and `RetrieveFilter.template` applies the same lens to both the
  records searched and the graph arm.
- **Five predicates, none domain-specific**: `teaches` — attribution, which the
  graph could not express at all — plus `leads_to`, `contrasts_with`,
  `caused_by` and `mitigated_by`. A causal chain rendered as `related_to` edges
  asserts the opposite of what an ordered chain says.
- **Predicates now carry a domain, a range and a confidence class.** An edge
  whose endpoint types the predicate does not permit is refused at write time
  rather than found later by someone reading a bad answer, and an edge reports
  whether its predicate is *structural* (stated plainly) or *interpretive* (a
  reading) — so a path resting on a reading can say so. Served from
  `GET /api/v1/graph/predicates` as `vocabulary`.

### Fixed
- **A long summary silently ate the graph.** `entities` and `relations` were
  last in the envelope schema, behind an unbounded `summary`, and a
  schema-constrained model emits in schema order. A templated extraction spent
  4,045 of a 4,096-token budget rambling inside `summary` and emitted no
  entities and no relations — an artifact with a title, a description, an empty
  graph and a state of `enriched`, with nothing reporting the truncation. The
  graph is now written first and the budget is 8192: a clipped summary is a
  worse summary, a clipped graph never existed.
- **Every edge Gemini produced was stamped confidence 0.5.** The Gemini schema
  dialect omitted the field although `RELATION_SCHEMA` carried it and
  `record_edges` read it, so the column said nothing.
- The predicate vocabulary was defined twice, in `graph.py` and
  `extraction.py`, with nothing failing if the two drifted — one list now.

### Migrations
- `0048_graph_templates.sql` — five predicates added to the `entity_edges` and
  `entity_facts` CHECK constraints; `template` on `data_items`, `entity_edges`
  and `entity_facts`. Run before deploying.

### Changed
- **"Interpret & rebuild" chooses its scope instead of asking you to type it.**
  Data type was a free-text box with three examples in the placeholder, for a
  closed set of twenty-four the server already publishes — and a typo there is
  not an error, it is a selector that matches nothing, previews *0 records*, and
  reads as an empty corpus. It now comes from `GET /api/v1/prompts`, the same
  registry the Prompts screen reads, so the two cannot drift. Tags are offered
  from the new `GET /projects/{id}/tags` when the project has any and left as a
  text field when it does not, since a select with nothing in it is a dead
  control and tags are an open set. Run stays typed — a run id is copied from
  Crawlers, not chosen from a set this screen can know — but the placeholder now
  says where to get one.

### Added
- **The model's keywords became usable.** Every enriched record already carried
  `artifacts.keywords` — the model's words for what it is about — read in exactly
  one place: beside a record you had already found. Now there is a GIN index, a
  `keywords` filter on `RetrieveFilter` (matching *any*, not all), and
  `GET /projects/{id}/keywords` counting records per keyword over what the caller
  can see, so a keyword whose every record is hidden does not appear. Chat can
  narrow by topic. **Kept separate from tags** — a tag is a person's assertion, a
  keyword is a model's guess, and merging them makes the guess unfalsifiable.

### Fixed
- **A freshly bootstrapped tenant refused every signed-in user at Add data**
  with *"this producer is bound to another user's personal connection"*. The
  bootstrap created a `personal` connection, which binds its producer to the user
  who ran it, and the console writes as the *signed-in user* rather than with a
  service credential — so only the bootstrap owner could write, while reads kept
  working and it presented as "adding data is broken". `cloudrun.sh` now
  bootstraps `shared`; a single-person deployment should pass `personal`
  deliberately.
- **The progress panel gave up on work that was going fine.** It watched for two
  minutes and reported "still queued", which was right when every job was one or
  two model calls — a long document is thousands of chunks and ~20 sequential
  embedding calls. The window now scales with the item (two minutes plus a
  minute per 200KB, capped at fifteen), rather than being raised for everyone: a
  ceiling generous enough for a book makes every genuinely stuck note look
  healthy. The message no longer leads with "still queued", because nothing has
  gone wrong when it fires.
- **The live viewfinder was blank while recording video.** The `<video>` renders
  only once recording has started, but the stream was attached before either
  state update had rendered — so the ref was still `null` and the `&& video.current`
  guard skipped the attachment silently. The camera light came on, the recording
  was fine, and nothing was shown. It is now attached from an effect that runs
  after the element mounts. The viewfinder also has its own `.viewfinder` class:
  `.preview` is defined twice in `globals.css` and the two merge, so a camera
  feed wearing it rendered at `opacity: 0.75`.
- **Recorded audio and video were stored but never interpreted.** Two faults:
  `video` was routed to the audio-only transcription model, which refused every
  clip with *"Image input modality is not enabled for this model"* and left it
  at `stored` (images were unaffected — they use the multimodal model); and an
  audio recording was classified as video, because `MediaRecorder` names both
  `capture-<ts>.webm` and the console dropped the mime, so the server fell back
  to the extension. **No audio item had ever existed.** A declared mime may now
  refine an ambiguous container — `audio/webm` over a sniffed `video/webm` —
  with the subtype required to match, so nothing can override a PDF. Existing
  stuck items recover with the ordinary enrich action; no re-upload.
- **A failed media call recorded the status and threw away the reason.**
  `raise_for_status` names the code and the URL only, so a row read
  `Client error '400 Bad Request'` while the provider had said precisely what
  was wrong. The provider's message is kept, and the console already renders it.

### Added
- **Chat can be scoped to the memories you point at.** `RetrieveFilter` gains
  `memory_ids`, so "what did we decide in the Acme thread" is answered by that
  thread instead of by everything the project knows. Filtered with `EXISTS`, not
  a join — a record in several selected memories would otherwise return once per
  membership and be ranked up for it.
- **Chat is a chat window.** The question appears the moment you ask rather than
  when the answer returns, the transcript scrolls inside its own bounds with the
  composer docked to it, and the answer is written out with a cursor. The reveal
  is pacing rather than streaming, and deliberately so: the answer is complete
  and checked against its citations before a word of it is shown.

### Fixed
- **`.card`, `.hint` and `.stack` had no CSS rule anywhere**, while being used 13,
  31 and 4 times — by Compaction, Alerts and AlertEditor. Those screens rendered
  with no container edge, no padding and no separation between blocks. Compaction's
  five actions also sat in a `<p>`, so they had no gap and Delete was flush against
  History.
- **[`docs/ingestion/templates.md`](docs/ingestion/templates.md)** — a design for
  extraction templates, not built. Extraction is routed by `data_type`, which is
  derived from the bytes, and the bytes cannot tell you what a document is *for*:
  a novel, a design doc and a contract are all `document` and are interesting for
  entirely different reasons. A template is declared intent, composed after the
  type block and never over the injection-defended skeleton, versioned like any
  other prompt so `/reprocess` already knows how to rebuild what it produced.
  Includes a catalogue across documents, media, images and tabular data, and the
  three rules that stop a template making things worse — chiefly that asking a
  model to "extract the obligations" is asking it to find some.
- **The text ceiling is a deployment setting** (`MAX_TEXT_CHARS`, default
  2,000,000; 4,000,000 here) instead of a constant, and **`/reprocess` gains a
  `parse` stage** that makes a raised ceiling reachable. Raising it alone changed
  nothing: the parse worker skips any row that already has text — correct for an
  at-least-once queue, but it cannot tell "already done" from "done under a
  smaller ceiling". The new stage clears the derived text and re-reads the bytes,
  which are untouched, so nothing is re-uploaded.
- **[`docs/ingestion/large-documents.md`](docs/ingestion/large-documents.md)** —
  what would have to change for gigabyte documents **and long media**, none of
  which is built. For media the useful distinction is that there are *two* walls
  and only one needs splitting: the 18 MB ceiling is the provider's *inline*
  limit and lifts by sending a reference instead of base64, while the duration
  wall needs segments however the bytes arrive. Records what splitting media
  costs that splitting text does not — a container cannot be cut arbitrarily, so
  it needs a media toolchain the API image does not have. The transport is already
  designed (`POST /uploads` grants a signed URL; bytes never pass through the
  API); the processing is not. Proposes splitting one upload into part-records
  inside one memory, which is the only change that makes failure partial and
  work resumable.
- **"How to use this" in the console**, pinned top-right: four steps end to end
  — get something in, watch it climb, get it back, prove it — then **a
  walkthrough for every one of the twenty-six sections**, expanded one at a time,
  each with a link straight into that screen. The steps were written against each
  screen's own panels rather than from its label, and where a screen has a trap
  the step says so: a minted signing secret leaves an inbound endpoint looking
  configured while it rejects every real delivery; deliveries are stored and not
  interpreted unless asked; a crawler's dry run walks the identical code and
  stops short of the write. Per-item text is read from the same `GROUPS.hint`
  values the sidebar uses, so the guide cannot drift from the menu it describes.

### Changed
- **The console sidebar is readable on sign-in.** It rendered twenty-six
  destinations across eight groups with every group open and every item showing
  a label *and* a hint — about sixty lines of text, all at one volume. The hints
  are now tooltips (each screen already states its purpose in its own lede), a
  **filter** matches label and hint together so "webhook" finds Inbound, and only
  the group you are in starts open. Every heading stays visible, any number of
  groups can be open, and the group holding the current section opens itself —
  collapsed is not hidden.
- **The sign-in page's bar is five beats, not eight anchors** — how it works,
  what it connects, what it tells you, what it accepts, why trust it — each
  covering a run of sections and named for what the page argues there rather
  than which feature lives in it. Groups cover *contiguous* sections only: the
  bar doubles as a position indicator, so one spanning a gap would light, go
  dark and light again as you scrolled through it.
- **The hero states the guarantee rather than the feature** — *"No answer
  without its source. No silence without its reason."*
- **The wordmark returns you to the top of the page.**
- **Add data is five steps and one button** — what kind of thing, the thing
  itself, where it goes, what is done to it, who may see it, then *Add data*.
  The action used to sit in the first card, above three of the four decisions it
  committed, and recording had a second write button of its own, so whichever
  you pressed you committed before reaching the settings. Capture now stages a
  payload and the screen commits it, so text and media end at the same button,
  with the destination, interpretation and audience on the line above it. Memory
  is chosen from the project's existing memories rather than typed as a
  free-text type and key. The last three steps are collapsed but state their
  current value, because closed should not mean hidden.

### Changed
- **The sign-in page is about half its former length** — ~1,700 words of prose
  down to ~930, with the fourteen section cards going 560 → 269. No claim was
  dropped; what went is the second sentence of almost every paragraph, the one
  explaining why the first mattered.
- **The top bar shows how far into the page you are**, as a rail along its own
  bottom edge, and marks the current section with a dot rather than a heavier
  underline. Driven by `scaleX` behind `requestAnimationFrame`, so a fast scroll
  costs nothing.
- **Three repetitions removed from the sign-in page.** The alerts story was told
  twice — a preview card and then the section covering the same ground; `RULES`
  and `FREE` each labelled two unrelated things; and three section titles began
  with "And", which reads as a fragment now that the bar links straight to them.

### Fixed
- **Admin → Platform said `credential lacks admin:*` and left it there.** No
  signed-in session ever carries that capability — the endpoint counts across
  every tenant, so an organization owner is deliberately not a platform
  operator. The screen now says so, says the rest of Admin is org-scoped and
  works, and names the operator command that grants it
  (`python -m memdog grant-key <prefix> 'admin:*'`). A failure that is *not*
  about the capability is reported as itself.
- The same screen rendered its results as `JSON.stringify` over
  `Object.entries`. Unpurged tombstones now read "a delete is recorded but the
  bytes are still there".

### Added
- **[docs/blog/README.md](docs/blog/README.md) — the negative space.** A short
  piece on the one bug report every retrieval system gets, *"it's missing
  something I know is in there"*, and the four unrelated causes it collapses:
  ranked too low, never indexed, not visible to you, not there at all. Reads the
  response shape that separates them — `excluded` with its two reasons, `corpus`
  by state, `matched_by` per arm, `generator_version` — and says why an ACL
  exclusion is the one that may never be listed, because naming a withheld
  result is the disclosure. It records one limit the README does not: the
  `not_yet_enriched` list stops at 25 rows, so it is a diagnostic and
  `corpus.stored` is the number to trust for scale.
  [docs/presentation/blog.md](docs/presentation/blog.md) stays the long read;
  this one links to it.
- **`npm test` in `ui/`** — the console had no test runner at all. Node 22 runs
  TypeScript directly, so this adds no dependency and no config. Ten tests cover
  `assess`, the function that decides what the write-progress panel says, moved
  to `lib/progress.ts` so it can be imported without a DOM. `npm run verify` now
  runs them.
- `tsconfig` included `**/*.ts` but not `**/*.mts`, so the new test file was not
  typechecked. Fixed — and it immediately caught fixtures cast through
  `as DomainEvent` while missing five required fields.
- **The proxy allow-list has tests**, and moved to `lib/proxy-allow.ts`.
  `check-proxy-paths.mjs` now imports the arrays instead of scraping the route
  file with a regex, which could not tell the three lists apart. Nine tests,
  mostly refusals: traversal, segment-swallowing ids, unopted query strings,
  unanchored patterns, and `.+` wildcards that span path separators.
- `ui/package.json` declares `"type": "module"`, so Node stops reparsing every
  `.ts` it loads and warning on each build.
- **The console's access ceiling now fails closed on an unknown connection
  scope**, matching `acl.py`, which caps every non-null scope other than
  `shared` at `private`. The console returned `public` for anything it did not
  recognise; that agreed with the server only because the database permits
  exactly two scopes. A third would have made the picker offer a level the API
  rejects. The rule moved to `ui/lib/acl.ts`, with tests on both sides — the
  API's names the UI file in its failure message.

### Changed
- Six plan files under `.claude/plans/` claimed *"plan only, nothing
  implemented"* for features that shipped — alerts, compaction, standing
  queries, workflows, the temporal graph and connector sync. They now say what
  is built and what is not.
- `graph.build.requested` is still emitted and still unconsumed, but the comment
  beside it no longer says the graph is unbuilt. **It is built** — `record_edges`
  runs inside the enrichment worker, in the same transaction as entity
  resolution. The stale comment produced two separate wrong diagnoses of an
  empty graph. The event now documents what it actually is: a marker that each
  write is a candidate for a rebuild pass nobody has written yet.

### Fixed
- **A connector declaring `method: POST` was issued as a bodyless GET.**
  `HttpRequest` has always carried `method` and `body`; the crawler read
  neither. Linear, Notion, Attio and Copper were affected — Linear is GraphQL,
  so it was not a degraded request but a meaningless one. Request bodies are
  now sent, and templated recursively, which is what makes an incremental
  clause expressible for any provider whose filter lives in the body.
- **14 of 37 connectors now pull incrementally, up from 1.** The rest re-read
  their whole source on every scheduled run — a cost and rate-limit problem
  that shows up on day two of a pilot and raises nothing while it happens. The
  remaining 13 are listed with the specific obstacle in `NO_INCREMENTAL`; a
  wrong filter parameter is silent, so a guessed clause is worse than none.
- A crawler-scheduler test failed under full-suite load and passed alone. The
  advisory lock is taken on a pooled connection and is re-entrant per session,
  so two racing ticks sharing a connection both proceeded. The lock holds
  between processes, which is how the scheduler runs.

### Changed
- **The sign-in page shows a result instead of describing one.** A panel renders
  the shape of an answer — the passages returned with the arm that matched each,
  and the records considered and *dropped* with the reason for each. It is
  labelled an illustration, since nothing is queryable before sign-in.
- **The top bar navigates the whole page and marks where you are.** It had been
  cut to two anchors; the count was never the problem, the missing sense of
  position was. `SECTIONS` is the single source of truth and the nav narrows at
  mount to ids that exist, so a renamed section loses its anchor rather than
  keeping one that scrolls nowhere. Below 560px the bar now scrolls sideways
  instead of disappearing.

### Fixed
- Anchor jumps on the sign-in page landed with the heading hidden under the
  sticky bar, which reads as a broken link. Every landing section now carries
  `scroll-margin-top`.
- **Every Twilio webhook would have been refused, permanently.** Twilio is the
  one provider that signs the *URL* rather than the body. Cloud Run terminates
  TLS and forwards over plain HTTP, so the reconstructed URL was `http://` while
  Twilio signed the `https://` address configured in their console — and the
  failure surfaces as `signature verification failed`, which reads as a wrong
  secret. `X-Forwarded-Proto` is now honoured when rebuilding the signed URL.
  No unit test could have caught it: a test hands the same URL to both sides.
  Found by firing `tools/fake_inbound.py` at the deployed service.
- The inbound route is `/webhooks/{producer_id}`. Two docs and the tool's own
  usage line said `/hooks/`, which is the console page, not the endpoint.

### Added
- **`api/tools/fake_sources.py`** — the four pagination mechanisms
  `fake_salesforce.py` did not cover, each because a real connector depends on
  it: a `Link` header that lists `rel="last"` first, `startAt` offset
  arithmetic, an absolute `@odata.nextLink`, a `page=N` source where only
  `stop_when` ends the crawl, and an RSS feed with a bare `&` in it. Run with
  `python -m tools.fake_sources`. It **counts requests**, and the tests assert
  on the count — the crawler's own `Budget` counts items, so nothing could tell
  five records in three requests from five in twenty, which is exactly how the
  Salesforce paging defect stayed invisible.
- **`api/tools/fake_inbound.py`** — a signer for all nine webhook providers,
  written from each provider's published scheme rather than from
  `providers.py`, so the two have to agree. It also sends a real signed POST at
  a running deployment: `python -m tools.fake_inbound github <url> <secret>`.
  Nothing else in the repo does that, so the HTTP layer of
  `/hooks/{producer_id}` had only ever been reached by an actual provider.
  The test iterates the registry, so **a provider added with no signing rule
  now fails on the day it is added.**

### Changed
- **The sign-in page drops its “Where the others are ahead” closing block.** The
  paragraph above the comparison already concedes the same two things — that
  self-hosting is not a differentiator and that Onyx is ahead on permissions —
  so the block restated them at length and ended the page on reasons to pick
  something else. The matrix keeps every unflattering cell.

### Added
- **`pagination.type: "next_url"`** — the source hands back the *whole* next URL
  in the body and `cursor_path` says where. This could not be templated before,
  and two catalog entries carried notes admitting they pulled one page.
  Salesforce returns `nextRecordsUrl` as a **path**, Microsoft Graph returns
  `@odata.nextLink` absolute; both work now. A next URL is attacker-controlled
  if the source is, and the crawler carries the connection's credential in its
  headers, so the resolved URL **must stay on the origin the run started
  against** — a source that genuinely pages across hosts is one this cannot
  crawl, which is the right way round.
- **`api/tools/fake_salesforce.py`** — a Salesforce-shaped API you can crawl
  without a tenant: the client-credentials exchange, the
  `{totalSize, done, records, nextRecordsUrl}` envelope, a real
  `WHERE LastModifiedDate >` filter, and paging behind a server-side query
  locator. `python -m tools.fake_salesforce` runs it on `127.0.0.1:8787`.
- **`exercised_against` on a catalog entry**, and the console now states it.
  `verified` still means somebody ran it against a live account and is still
  false everywhere; this is the weaker claim that can be earned without a
  credential — the template was *run*, against a named thing that exists, which
  a test checks. The console previously said nothing at all about verification
  while the docs claimed it did.
- **Two Secret Manager secrets for console sign-in** — `memdog-owner-password`
  and `memdog-demo-password`, on `memdog-dev-506718`. The two Identity Platform
  accounts existed with memberships already; their passwords had never been
  recorded anywhere, so neither could actually be used. These are the only copy.
  The deploy skill also now writes down the three conditions a sign-in needs,
  because auto-provisioning satisfies two of them and the third fails quietly:
  it creates a user and an identity but **never a membership**, so a new account
  authenticates cleanly and then gets `403 not a member of any organization`.
- **A meeting transcript is written restricted to the room**, not to whatever
  its connection scope would give it. That inheritance is right for a Jira
  ticket and a **serious disclosure for a recording** — four people in a room
  did not publish to the company, and nothing downstream knows the difference.
  Opt-in per integration via `attendees_path` in the producer's mapping, since
  one key that silently re-ACLed a project's whole ingestion would be a worse
  bug than the one it fixes.
- **Zoom, Meet and Teams are configuration, not three code paths.** They differ
  only in where the attendee list sits and what the key is called, so a
  provider nobody has heard of works on the day it arrives. Two conservative
  directions: an attendee outside the organisation resolves to **nothing**
  rather than having a principal invented, and a meeting where nobody resolves
  is written **private** — `restricted` to no principals is refused, and
  falling back to the connection default is the disclosure this prevents.
- **A transcript parser — `.vtt` and `.srt` — that produces turns, not cues.**
  Indexing cues is the wrong unit twice: a sentence spans three of them so no
  chunk holds a whole thought, and the timestamps outnumber the words. Speaker
  attribution accepts `<v Name>` and a `Name:` prefix, and **the heuristic runs
  the safe way**: a four-word name is read as unattributed speech, losing
  attribution, rather than reading *"One thing was clear:"* as a speaker and
  inventing it — invented attribution in a transcript is a quote put in
  somebody's mouth.
- **A `meeting` memory type**, ninety days and `archive`. The plan's caveat that
  TTL is unenforced is obsolete: the sweep shipped the same day, so this is a
  retention policy that runs.

### Fixed
- **The Salesforce connector re-read page one until it hit the page limit.** It
  put `nextRecordsUrl` into a query parameter, and Salesforce returns a path —
  so a crawl of four accounts fetched twenty pages, reported forty items, and
  looked entirely successful. Reading the config could not find this; running it
  against the simulator did, in one assertion.
- **The Salesforce `soql` scope now ships an incremental clause**, so a run
  reads what changed rather than the whole object every time. It is
  `WHERE LastModifiedDate > {{ watermark_or_epoch }}` — the `_or_epoch` half
  matters, because plain `{{ watermark }}` renders empty on the first run and
  Salesforce answers `MALFORMED_QUERY`, which would work on every run except the
  one that sets it up. `{{ watermark_or_epoch }}` is available to any template.

### Added
- **Workflows — long-running state machine instances, as a system of record.**
  The engine lives outside and calls in; memdog holds the state, the history
  and the deadlines and executes nothing. **A directed state graph that may
  contain cycles**, not a DAG: `review → reject → draft` is a workflow, and a
  DAG cannot express it.
- **`POST /instances/{id}/input` is the only verb that moves state**, and the
  update is conditional on the sequence the caller believed it was acting on.
  Two actors racing resolve as **one winner and one `409` carrying the state it
  actually found** — rather than two transitions out of the same state, which
  is corruption a state machine cannot survive and cannot detect afterwards.
- **The history is the record; `current_state` is a cache.**
  `GET /instances/{id}/verify` re-folds the log and reports sequence gaps and
  chain breaks — a denormalisation nobody can check is one people stop trusting
  the first time something looks wrong.
- **Validation refuses at definition time what cannot be fixed later**, since
  an instance pins the version it started on: an unreachable state, a
  transition to a state that does not exist, a terminal state with a way out,
  and **a TTL with no `on_timeout`** — a deadline with nowhere to go fires
  forever. Cycles pass, deliberately.
- **A budget per instance**, because an infinite loop is indistinguishable from
  a long legitimate one except by a number — and it is marked `errored` rather
  than silently refusing, since an instance that stops moving for no stated
  reason is the state nobody can diagnose.
- **`@timeout` is not addressable from a request**, so nobody can claim the
  clock fired; and `requires_actor_kind` keeps a key from performing an
  approval a human is meant to perform. Deadlines fire on the sweep that
  already runs.

### Migrations
- **`0044_workflows.sql`** — `workflow_definitions`, `workflow_instances`,
  `workflow_transitions`, `workflow_instance_members`. Additive. A transition's
  `data_id` is **nulled** rather than cascaded on erasure: erasing an email must
  not erase the fact that the order was approved.

### Changed
- **A connection's scope is now a ceiling, not a default.** `ItemAccess` has
  promised since the first release that a caller may never widen visibility and
  that a forbidden level is a **rejected item** — and nothing checked, so a
  producer reading somebody's personal mailbox could ask for `public` and get
  it. Enforced in `acl_for_write`, the one place an ACL is assigned. A producer
  with **no** connection is unrestricted, which is the rule rather than an
  exception: a direct client write has no scope to exceed.
- **`bootstrap` no longer attaches a personal connection to the operator's own
  API key** — in the library *and* in the CLI, which had its own default.
  Harmless while a scope was only a default; under a ceiling it silently caps
  every write made with that key at `private`, and it is why the console's
  producer on an existing deployment cannot publish to its own organisation.
  **Existing deployments need that producer's connection re-scoped or
  detached.**
- **The console offers only the levels the producer may write.** Wider ones are
  disabled and named, and a producer with a connection says so — a control that
  offers what will be refused is worse than one that offers less.

### Added
- **Standing matches are pushed, through the sender the alerts already use.** A
  monitoring product whose only delivery is polling is not finished. Same
  signing, backoff, dead-lettering and SSRF re-check — a second pipeline would
  need its own version of each, and **four controls are only worth something
  when they are the same four everywhere**.
- **The subject of a delivery is one of two things**, as two nullable foreign
  keys with a `CHECK`, not a polymorphic `(kind, id)` pair: the FKs are what
  make a delivery vanish when its subject is erased, and **a dangling reference
  there means a record the platform promised was gone being POSTed to somebody's
  URL**.
- **`kind` on a subscription.** `alert_id IS NULL` has always meant *every alert
  in this project*; letting it also mean *every standing query* would start
  posting a payload shape a subscriber registered last month has never seen.
- **Visibility is re-resolved at send time against the subscription's owner** —
  a different person from the query's owner, whose rights may have changed. A
  match they can no longer see is not a retry, it is a delivery no longer owed.
  The body carries a preview and ids, **never the record**: a webhook body is
  the least controlled copy of anything here.

### Fixed
- **A match found this minute waited until the next one.** The sweep evaluated
  standing queries *after* the tick that sends what is owed — a minute of
  latency that reads as a slow sweep rather than as two steps in the wrong
  order.

### Migrations
- **`0043_standing_delivery.sql`** — `event_subscriptions.kind` and
  `standing_query_id`, `event_deliveries.match_id`, and a partial unique index
  for at-least-once deduplication. Additive; every existing row keeps its
  meaning.

### Added
- **Standing queries (W10) — the one primitive that speaks first.** Say once
  what you want to be told about; each new record is checked against it as it
  arrives. Four published use cases were blocked on this and **media monitoring
  is only this**, so shipping it without delivery shipped nothing.
- **The matcher is `websearch_to_tsquery`, not an alert condition.** Alerts
  match on an event's payload; this matches on the item. Reusing `contains`
  over a preview would mean no stemming and no phrases — **missing "Acme's" and
  matching "acmeism"** — and a brand monitor that cannot find a plural is not
  one. Quoted phrases, `or` and `-exclusion` work, and it is the same lexical
  engine retrieval uses, so a query and a search agree about what words mean.
- **It never re-scans.** Each item is seen exactly once, walking forward from
  the sequence the query was registered at — starting at zero would replay the
  whole corpus into a feed on the first tick. Looking backwards is what the
  **backtest** is for, and enabling before one is a `409`.
- **Delivery is a read.** A match handed to somebody who cannot see the record
  is a leak through the notification channel. Visibility resolves under the
  **owner's rights at match time**, and a withheld match is **recorded and
  counted** rather than dropped: a feed that silently omits what it could not
  deliver is incomplete in a way nobody can explain.
- **Delivery into a memory needs no network** — no URL, no secret, no retry,
  and the memory it fills can then be rolled up, compacted, expired and alerted
  on. Poll feed alongside it; webhook push is the next commit.
- **Time is not a selector, and the screen says so.** *"Thirty days before a due
  date"* cannot be a predicate over new writes because nothing arrives that
  day — that is a scheduled sweep over dates, which expiry already
  demonstrates. Conflating them is how this engine would quietly become a
  scanner.

### Migrations
- **`0042_standing_queries.sql`** — `standing_queries`, `standing_runs`,
  `standing_matches`. Additive. Rides the existing minute sweep; no new job.

### Fixed
- **A compaction job on a parent memory reported "0 members"** beside a run that
  would consider three — the count was single-level while the run reads through
  `part_of`. The card is what somebody reads before deciding whether to run it,
  so **a count that disagrees with what the job does is worse than no count**.
  This was the third single-level reader of `memory_members`; the other two are
  defensible, since a listing showing a container's own members has the tree
  beside it.

### Changed
- **`memories.md` says what the two hierarchical relations actually do.** It
  described `memory_links` as a table with five relations and never said that
  `part_of` and `derived_from` behave nothing alike — a container has no
  separate state and can never be stale; a generated rollup is made *wrong* by
  the same change. The plan file also still opened *"plan only, nothing
  implemented"* after all six steps had shipped.

### Added
- **A rollup says when it is out of date.** A membership change marks every
  `derived_from` ancestor stale — deterministic, free, and it cannot be wrong.
  `part_of` is untouched on purpose: a container's members *are* its children's,
  so there is no separate state to go stale. **Nothing recomputes on write**,
  which is the correction the alert system already had to make; the mark is the
  signal and the recompute is a decision, taken through compaction with its
  existing preview gate. A **preview leaves the flag alone** — one that cleared
  it would be a preview with a side effect.
- **A flag, not a queue entry.** One import moving forty children would enqueue
  forty recomputes of the same rollup. Marking is idempotent, so it costs one
  row write however many children moved, and `stale_since` keeps the **first**
  change: *how long has this been wrong* is the question, not *when did it last
  get worse*. The walk is `DISTINCT` — two children rolling into one parent is
  the requested shape, so a diamond must not mark the shared ancestor twice.

### Fixed
- **Compacting a `part_of` parent considered nothing and reported success.**
  Its members are its children's, so a single-level `WHERE memory_id = $1` found
  none — a run over zero records that completes is the worst available outcome.
  It reads through the hierarchy now, deduped by `data_id`, since a record held
  by a child and its parent is one member and was otherwise folded twice.
  **The rollup's ACL follows for free**: the summary takes the strictest level
  among the sources it read, so one over four child memories is visible only to
  whoever can read all four.
- **`.notice.caution` was used twice and defined nowhere**, so the modifier
  meaning *this one is more serious* rendered identically to the notices it was
  distinguishing itself from — since the account-deletion screen shipped.

### Migrations
- **`0041_memory_staleness.sql`** — `memories.stale_since`, `stale_reason`, and
  a partial index over the only query that reads them. Additive.

### Added
- **Groups can be created, and shared with.** A group is a principal the ACL
  predicate has always resolved — the mechanism behind permission-aware
  retrieval — and **there was no way to create one**, so `restricted` to a
  group was unreachable in practice. Creating them was half the loop: nothing
  changes an item's ACL after the fact, so Add data now sets the level and its
  principals, which is the only moment that decision can be made.
- **The erasure certificate is displayed.** `GET /data/{id}/erasure` re-queries
  every table holding item-scoped data rather than trusting the cascade ran —
  the cascade being the thing under test — and is issued against `purged_at`,
  not `deleted_at`: **a tombstone is a promise and the purge is the thing that
  kept it**. The screen names the tables it checked, because a certificate that
  says only *complete* is the claim it exists to replace.

### Fixed
- **A principal is prefixed — `group:grp_…`, `user:usr_…` — and a bare id
  matches nothing.** Found by walking the new picker: the record is written,
  stored correctly, and **invisible to everyone including the person who wrote
  it**, with nothing anywhere saying why.

### Known
- **`ItemAccess` promises a rule that is not enforced.** It says a caller may
  narrow visibility and never widen it, and that a level the producer's
  connection scope does not permit is a rejected item — and `acl_for_write`
  does not check, so a personal-connection producer can request `public` and
  get it. Implementing it changes behaviour the suite and the seed depend on,
  which makes *which levels a scope permits* a policy decision rather than a
  bug fix.

### Added
- **An API key can be revoked, and issued as something other than
  `data:read`.** The Keys screen created keys and could not revoke one — the
  half that matters after a laptop goes missing. A revoked key stays listed
  rather than vanishing: it answers *what was this allowed to do while it
  worked*, which is the question asked after it leaks.
- **Invites have a surface.** `registration_mode` defaults to `invite_only`, so
  this is **the entire path by which a second person joins a deployment**, and
  it existed only as four endpoints. Issue, list and revoke, with the token
  shown once and said to be — and an empty state that separates *none
  outstanding* from *you are not an admin and would not see them either*.
- **A producer can be enabled and disabled from the console.** Disabling says
  what it does rather than what it sets: **deliveries are still accepted and
  dropped**, because a provider handed a `4xx` retries forever or gives up
  silently, and neither is what you meant.
- **Freshness is shown per scope.** `source-lag` shipped three commits ago with
  no reader, while the Producers screen showed one number per producer — and a
  crawler over forty channels is forty sources behind one number, so a single
  busy channel kept it looking healthy while thirty quiet ones went unread.
  **Cooling is rendered separately from broken**: a rate-limited credential is
  waiting exactly as long as it was told to.
- **The alert editor reads the predicate vocabulary** from
  `GET /graph/predicates` instead of the `located_in` it had typed into its own
  default condition, and names the **single-valued** predicates — the
  difference between a second fact closing the first and a second fact
  accumulating, which decides whether a rule sees `fact.superseded` or
  `fact.asserted`.

### Changed
- The endpoint-caller exemption list drops from **29 to 24**. What remains is
  deferral rather than omission: groups, schema editing, presigned upload,
  manual fact assertion, the erasure certificate.

### Added
- **A test that fails when an endpoint has no caller.** Four defects in one day
  shared a shape — `POST /data/{id}/enrich`, `POST /reprocess`,
  `GET /artifacts/stale` and `PUT /settings/{scope}/{key}` all existed, were
  correct, had tests and documentation, and **were reached by no client we
  ship**. Each presented as *we never built that*. `test_wiring.py` already
  guarded a setting nothing reads and a column nothing mentions; this is the
  sibling it was missing, and `scripts/check-proxy-paths.mjs` does the mirror
  direction, so the loop is closed.
- **It found 29 of 144, and the exemption list is the inventory.** A few are
  correct by construction — a share link is opened by whoever received it, and
  the ephemeral-token exchange is for embedding hosts rather than our own
  console. **Most are gaps now named**: invites have no surface though
  registration is invite-only by default, an API key can be issued and not
  revoked, a producer's status is shown and cannot be changed, `source-lag`
  shipped with no reader, and the alert editor types in a predicate that
  `GET /graph/predicates` exists to supply. The list is a ratchet — it may
  shrink, and wiring something while leaving it listed fails the other half.

### Added
- **An alert on a parent memory now sees changes in its children.** The scope
  was one row — `WHERE memory_id = $1` — so it was single-level, and **a scope
  that silently means less than it says is the same failure as one that
  silently means more**. It is resolved by walking `part_of` downward at match
  time: no new writes, no duplicated events, no propagation storm. Emitting a
  parent transition per child write would cost an event per level per item and
  is what the alert system already had to undo.
- **`GET /api/v1/memories/{id}/tree`** — ancestors and descendants, kept apart
  because *what rolls up into this* and *what this rolls up into* are different
  questions. Depth- and node-limited, and both limits report themselves: a
  silently truncated tree has the same shape as one that really is that deep.
- **A link that would close a cycle is refused** (`409`), at the edge that
  closes it rather than survived by every recursive reader. Per relation — a
  memory derived from another can also be part of it.
- **The console builds and shows the hierarchy**: where a memory sits, a
  control to make it part of another, and *"includes N child memories"* beside
  a hierarchical alert scope — read from the same walk the matcher uses, so the
  sentence cannot drift from the behaviour.

### Fixed
- **`{"op": "exists"}` meant *does not exist*.** The operator compares against
  `bool(value)` and `validate` deliberately permits the value to be omitted for
  exactly this operator, so the obvious way to write it inverted the condition.
  **A rule that matches nothing is indistinguishable from a quiet week**, which
  is the failure alerts exist to remove. Say `{"op": "exists", "value": false}`
  for the opposite.

### Added
- **TTL is enforced.** `ttl_seconds` and `on_expiry` have been storable,
  editable and computed since the memories slice shipped while **nothing
  swept** — a `conversation` memory with a one-hour TTL was still there a year
  later, and this changelog has carried it as *not built* through two releases.
  The sweep runs with the scheduled reconcile, and on demand via
  **`POST /api/v1/expiry/sweep`** where `dry_run` defaults to **true**.
- **An item is due only when every memory holding it has expired**, and the
  policy that applies is the one belonging to the membership that expired
  **last** — the container that kept it alive. Sweeping per membership would
  delete a record a permanent memory still depends on, which is the
  `orphan_delete` bug in another form.
- **`orphan_delete` goes through the ordinary deletion cascade**, so an expired
  record gets the same tombstone, blob reclamation, legal hold and audit trail
  as any other erasure. `archive` stamps the column compaction already uses;
  `keep_members` re-files into the default **and drops the expired
  memberships**, or the item is due again on the next pass forever.
- **`GET /api/v1/projects/{id}/expiring`** — what is due and under which
  policy, because a retention policy nobody can inspect before it runs is one
  nobody will turn on. Shown on the Memories screen with a preview and a sweep.

### Changed
- **The scheduled pass sweeps per owner, not once as a superuser.** The
  deletion cascade selects under the caller's own visibility, so a single
  privileged-looking pass would **silently skip every private record** —
  exactly the ones with the tightest retention need — and report a clean sweep.
- **The reconcile job now registers the delete worker and drains
  unconditionally.** It publishes deletions now: a tombstone whose reclamation
  message nothing consumes leaves chunks, embeddings and blobs behind for a
  record already promised gone.

### Migrations
- **`0040_expiry_actor.sql`** — `audit_events.actor_mode` admits `expiry`.
  Additive, and the constraint is replaced rather than edited, as `0020` did
  for `crawler`. Filing an unattended erasure under `platform` would put an
  operator's fingerprint on something no person did.

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
