# Use Case Catalog

The public site publishes **twelve** use cases. This document takes each one and states how it is
implemented — what arrives, which path ingests it, which memory type holds it, which agents and
indexes serve it, and what the query looks like.

It exists because a published use case is a **commitment**. Six of the twelve are already covered
by the [worked scenarios](use-cases.md); three need machinery that is not yet designed; one is
explicitly out of v1 scope. Naming which is which is more useful than claiming all twelve are
equally supported.

> These are surfaces, not architectures. All twelve run the same pipeline — the
> [five families](use-cases.md#the-five-families) remain the architectural grouping. What changes
> between them is the connector, the memory type, the agent's output schema and which index is
> primary.

---

## Coverage at a glance

| # | Published use case | Family | Primary index | Status |
|---|--------------------|--------|---------------|--------|
| 1 | Personal Knowledge Base | A | vector + timeline | **Designed** — depends on channels, cut from prod v1 |
| 2 | Team Memory | B | hybrid + graph | **Designed** — source-ACL sync is the known gap |
| 3 | Customer Intelligence | B | facets + claim | **Designed** — needs generator-pinned sentiment |
| 4 | Research & Analysis | A/B | **claim index** | **Designed** — claim index deferred past MVP |
| 5 | Compliance & Audit | E | version + access log | **Designed** — no alerting |
| 6 | IoT & Sensor Data | B/D | facets only | **Partly** — standing queries are built; a threshold over a facet is not |
| 7 | Legal & Contract Intelligence | B/E | facets + case | **Built** — a date rule fires ahead of a deadline; obligations are one of the generators |
| 8 | Healthcare & Clinical Notes | B/E | case + facets | **Partly** — imaging out of v1 |
| 9 | Education & Training | A/B | derived artifacts | **Built** — six generators over a memory's members |
| 10 | Sales Enablement | B | summary hierarchy | **Designed** |
| 11 | Media Monitoring | A/B | **standing queries** | **Built** — matched on arrival, delivered by poll, memory or webhook |
| 12 | Meeting Intelligence | B | intent index | **Designed** — media gated on a v1 decision |

Two structural gaps fell out, and **both are now closed** — the section at the
[end of this document](#the-two-gaps-this-catalog-exposes) records what they were and what was
built. Neither was large. Both were invisible until the twelve were written out, which is the
argument for writing them out.

---

## 1 · Personal Knowledge Base

> *"Capture from WhatsApp, email, Slack with semantic search"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | Chat messages inline (text fits the envelope); Gmail as a `historyId`, not content |
| **Ingest** | Channel producer `whk_` posts `Inline` to `/api/v1/write`. Gmail posts `Pending` → **W2 fetch** resolves the history range, then fans out message + attachments as separate jobs |
| **ACL** | Connection scope `personal` → every item is `private` **regardless of project defaults** |
| **Memory** | Routing rule on thread id → `conversation` (1h TTL). Promoted to `factual` when it turns out to hold something durable |
| **Enrichment** | W1 at the cheap tier — breadth over depth. On self-hosted with a local model this costs nothing |
| **Indexes** | Vector + BM25 + timeline memory |
| **Query** | *"When did I promise Sam the deck?"* → hybrid retrieval narrowed by the intent index |

**What makes it work:** connection-scoped ACLs. Personal Gmail inside a team org must not become
team-visible, and no project-level default can express that — only the connection can.

**Honest note:** messaging channels are the differentiator here and are **cut from production v1**.
On v1 this use case is email and Slack only.

---

## 2 · Team Memory

> *"Shared org memory across channels with auto-classification"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | Shared Slack, Drive, Notion, GitHub — connections declared `shared` at connect time |
| **Ingest** | Connector sync + webhooks. Google Docs use the **`export` verb**, not download — a Doc has no raw bytes |
| **ACL** | Inherited from the connection. Member-visible by default |
| **Memory** | `organizational` (no TTL) for durable knowledge; `session` for meeting series |
| **Enrichment** | Auto-classification is the agent's typed output schema — a normalized facet, not a guessed tag string |
| **Indexes** | Hybrid + **temporal graph** — *what was true when* |
| **Query** | *"Why did we move off the old queue?"* → graph traversal with conflict surfacing when a doc and a later thread disagree |

**The gap, stated plainly:** a connector that ingests everything a user can see and then exposes it
to the whole org is a data leak by construction. Private Slack channels and ACL'd Confluence spaces
need their **source ACLs synced and applied pre-retrieval**. The design for this exists
(connection-scoped inheritance, query-time filtering); Onyx has shipped it. See
[competition/comparison-onyx.md](competition/comparison-onyx.md).

---

## 3 · Customer Intelligence

> *"Ingest support tickets, CRM, chat logs with sentiment analysis"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | Zendesk / Intercom tickets, Salesforce / HubSpot records, chat transcripts |
| **Ingest** | Connector sync; tickets `read` as structured records, attachments materialised separately |
| **Correlation** | **Case** with `case_type: customer`, `external_id: <account id>`. Joins on the identifier that already appears in every system — *not* entity resolution |
| **Memory** | `organizational`; each ticket also joins a short `conversation` memory for its thread |
| **Enrichment** | Sentiment is a **typed field on the agent's output schema** → a normalized facet with a bounded range, not prose to grep |
| **Indexes** | Facets (sentiment, severity, product area) + vector + **claim index** |
| **Query** | *"How has Acme's sentiment moved since the outage?"* → facet range ordered by `event_time` |

### Finding — a sentiment trend across two generator versions is a fabricated trend

Sentiment is a model output. Re-tune the prompt, swap the model, change the parser — the score
moves for reasons that have nothing to do with the customer. Plot a year of it and you get a
trend line that is partly the customer and partly your own release history, with no way to tell
which is which.

The mechanism already exists: **`generator_version`** — `sha256` over prompt, model id, schema,
parser version and chunker version — plus `served_by_model` recording what actually answered.
The requirement is to *use* it here:

- A trend query MUST filter to a single `generator_version`, or explicitly mark the boundary.
- Changing the sentiment generator MUST enqueue **W7 reprocess** for any memory serving trends,
  or the series is knowingly discontinuous.

This applies to every numeric agent output, not just sentiment. Sentiment is where it bites first
because it is the one people put on a chart.

---

## 4 · Research & Analysis

> *"Ingest PDFs, papers, web pages with AI viewpoints"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | PDF uploads (`upl_`), web pages and preprints via **crawler** (`crw_`), RSS |
| **Ingest** | Upload → `Stored` ref directly. Crawl → **W3** discovers, emits `Pending`, **W2** materialises. `robots.txt` respected; scope bounded by depth and per-root job budget |
| **Formats** | PDF → text with layout. Academic PDFs need **section-aware chunking** so a claim and its citation are not split across chunk boundaries |
| **Memory** | `research` — no TTL, `keep_members`. One memory per literature review, populated by **selector**: *everything tagged `topic:x` from the last two years* |
| **Enrichment** | The viewpoint agent emits claims with **span offsets into the source document** |
| **Indexes** | The **claim index** is primary here, not vector. Contradiction detection across papers |
| **Query** | *"What do these forty papers disagree about?"* → claims grouped by assertion, conflicts surfaced with citations |

### Finding — citations do not survive compression unless offsets do

A viewpoint that says *"three papers dispute this"* is worth nothing without the ability to open
each one at the sentence. That requires a span offset into the source.

Compression archives originals behind a summary. [Privacy foundations](security/privacy-foundations.md)
already requires the summary to record its **member set as a list**, so a later erasure can find
and rebuild it. Citations need the same list to carry **offsets**, not just document ids — otherwise
the summary can name its sources but cannot point into them, and every citation in a compressed
research memory degrades to a document-level reference.

Same list, one more field, decided now rather than after the first compression runs in production.

---

## 5 · Compliance & Audit

> *"Versioned mutations with immutable audit trail"*

| Stage | Implementation |
|-------|----------------|
| **Mutation** | **W8 mutate** — every change writes a new version; nothing is edited in place. Diffs are queryable |
| **Audit** | Append-only access log: who read what, when, under which principal and which key |
| **Retention** | Memory type TTL + `on_expiry`, with legal hold suppressing expiry entirely |
| **Erasure** | Full cascade — chunks, embeddings, blobs, entity contributions, derived artifacts marked stale |
| **Query** | *"Who accessed this record in March?"* → the access log, which only answers if you were recording |

**The retrofit trap:** access audit cannot be added later. There is no way to reconstruct who read
a record in March if March was not logged. This is why family E is built early despite ranking last
in urgency.

**Gap:** compliance wants *"tell me when a record in retention class X ages past Y"*. Retrieval is
pull-only. See [the two gaps](#the-two-gaps-this-catalog-exposes).

---

## 6 · IoT & Sensor Data

> *"GPS, biometric, weather, industrial sensor processing"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | High-volume structured readings — the [telemetry example](examples/telemetry.md) runs 29M records/day |
| **Ingest** | Bulk write with `embed: false`, `enrich: false`. **Zero records reach a model** |
| **Memory** | `tracing` (3d TTL, `orphan_delete`) for raw readings; derived aggregates land in a durable memory with a `derived_from` link back |
| **Enrichment** | None on the raw path. Aggregation is a scheduled job, not an agent |
| **Indexes** | Facets only — device id, metric, value range, time. No vectors |
| **Query** | Facet range over a time window |

**The design constraint is cost, and the failure mode is obvious in hindsight:** routing every
sensor reading through an LLM. Admission control decides `embed` and `enrich` **per producer**, not
per item, so the decision is made once at registration rather than 29 million times a day.

**Gap:** the actual use case is *"alert me when the freezer goes above −18°C"*. That is a
subscription, not a query, and nothing in the design delivers it.

---

## 7 · Legal & Contract Intelligence

> *"Extract clauses, obligations, deadlines from contracts"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | Contracts and correspondence — upload, email, DMS connector |
| **Correlation** | **Case** with `case_type: matter`. Documents *asserted* into a matter are authoritative; documents *inferred* by shared entities are **suggestive only** and must never drive an access decision |
| **Enrichment** | Clause, obligation and deadline extraction is a **typed agent output schema** — structured fields, not prose |
| **Indexes** | Deadlines become **date facets**, so *"what is due in the next thirty days"* is a range query rather than a search |
| **Retention** | **Legal hold beats erasure.** A hold suppresses `on_expiry` and causes an erasure request to be *refused with a stated reason* rather than silently partially applied |
| **Query** | *"Which contracts have an auto-renewal clause firing this quarter?"* → facet intersection |

See [examples/legal.md](examples/legal.md) for the worked matter, including the hold/erasure conflict.

**Gap:** an extracted deadline is a date that needs to **fire**. Same missing primitive as §5 and §6.

---

## 8 · Healthcare & Clinical Notes

> *"Process medical records, imaging, lab results"*

| Stage | Implementation |
|-------|----------------|
| **Correlation** | **Case** with `case_type: patient`, `external_id: MRN`. Joins on the MRN — two patients named John Smith **must never merge**, which is why this is an identifier join and not entity resolution |
| **Lab results** | Structured → normalization → facets **carrying units**, not bare values |
| **Imaging** | DICOM as a `Stored` ref with a mime type. Storable and retrievable today; **interpretation is out of v1 scope** |
| **Model routing** | Per-project allowed-provider list. The fallback chain **fails closed** rather than falling through |
| **Query** | Patient timeline ordered by `event_time`, spanning notes, labs and correspondence |

### Two findings this use case forced

**The fallback chain crosses a legal boundary.** A routing chain that degrades from a
BAA-covered provider to an uncovered one turns a capacity event into a HIPAA disclosure. Nothing
errors; the answer comes back fine. The chain must therefore be **allow-listed per project and
fail closed** — refusing to answer is the correct behaviour when every covered provider is
unavailable.

> **A capable open medical model changes the shape of this.** Running something like MedGemma
> locally removes the sub-processor entirely — there is no third party to sign an agreement with,
> because the content never leaves the deployment. The allow-list is still what makes it *true*
> rather than merely *possible*: a clinical project permits local engines only, and the chain fails
> closed. See [model-catalog.md](operations/model-catalog.md). It also puts DICOM interpretation —
> currently out of v1 — back within reach.

**Unit normalization is where lab data goes wrong.** Glucose in mg/dL and mmol/L differ by a
factor of eighteen. A facet storing the number without the unit produces a range query that is
confidently, silently wrong. The normalization schema carries units as part of the value, not as
an adjacent metadata string.

See [examples/medical.md](examples/medical.md) for the worked patient timeline, and
[cases.md](cases.md#what-exists-today) for the part of it that is built — declaring the patient,
filing records against the MRN, and reading the history back in `event_time` order.

---

## 9 · Education & Training

> *"Generate study guides and flashcards from course materials"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | Course materials — PDFs, slides, recorded lectures |
| **Memory** | A `course` memory with **static** membership: a curated set, explicitly assembled |
| **Generation** | A study guide and a flashcard deck are **derived artifacts over the memory's member set** |
| **Staleness** | Adding a member marks derived artifacts stale; **W7 reprocess** rebuilds them |
| **Query** | Retrieval scoped to the course memory, plus the generated artifacts as first-class records |

### This use case needs a generalisation — and it is a small one

Today a memory supports exactly one derived artifact:

```http
POST /api/v1/memories/{id}/compress     → produces a summary
```

A study guide is that same operation with a different output schema. So is a flashcard deck, a
timeline digest, an obligations extract (§7) and a customer briefing (§3). Hardcoding "summary" as
the only output means every one of those becomes a bespoke endpoint.

**Generalise `compress` into `derive`:**

```http
POST /api/v1/memories/{id}/derive   { "generator": "flashcards" }
POST /api/v1/memories/{id}/derive   { "generator": "summary" }     ← what compress was
```

The registry this needs **already exists**. `generator_version` is defined as
`sha256(canonical_json({prompt, model_id, schema, parser_version, chunker_version}))` against an
immutable generator registry, built for enrichment agents. Extending it from item-level enrichment
to memory-level artifacts is a scope change, not a new mechanism — and it brings three properties
along for free:

- **Reproducibility** — a flashcard deck records the generator that produced it
- **Staleness** — membership changes already invalidate derived artifacts
- **Erasure** — derived artifacts already record their source set as a list, so a removed member
  cascades correctly

Compression stays the *policy* that archives originals behind a derived artifact. It stops being
the only artifact you can derive.

**Status: this is the one genuine design change in the catalog.** Recommend adopting it — it
converts three separate future endpoints into one.

---

## 10 · Sales Enablement

> *"Summarize deal history across Salesforce and HubSpot"*

| Stage | Implementation |
|-------|----------------|
| **Correlation** | **Case** with `case_type: deal`. The same deal in two CRMs correlates by declared `external_id` — never by fuzzy name matching, which merges "Acme Corp" and "Acme Corporation" and also merges two genuinely different Acmes |
| **Memory** | `organizational`, with `about` links to the deal case |
| **Enrichment** | Commitments extracted into the **intent index** as structured fields |
| **Indexes** | **Summary hierarchy** — the "catch me up on Acme" path |
| **Query** | *"What pricing did we commit to, and who raised the security objection?"* |

Worked in full as [scenario 1](use-cases.md). The load-bearing detail is that Zoom recordings
**fan out** into video, audio, transcript and chat as four separate items — the transcript is what
answers the question, and it arrives as a distinct fetch job.

---

## 11 · Media Monitoring

> *"RSS feeds and social media with sentiment analysis"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | RSS feeds and social APIs — both **scheduled polling**, W3 on an interval, W4 renewing any subscriptions |
| **Idempotency** | Keyed `(provider, resource_id, etag \| checksum)`. A re-polled feed is a cache hit, not a duplicate ingest — without this, polling a feed hourly ingests every item twenty-four times a day |
| **Memory** | Short-TTL firehose memory; items matching a watch are **promoted** to a durable memory rather than copied |
| **Enrichment** | Sentiment and entity extraction, subject to the generator-pinning rule from §3 |
| **Delivery** | **← this is the problem** |

### This use case breaks the pull-only retrieval model

Nobody sits and queries a media monitor. The product *is* the notification: define what you care
about, get told when it appears. Every retrieval surface in the design is pull — a caller asks, the
system answers.

Two of the three required pieces already exist:

| Piece | Status |
|-------|--------|
| A saved, bounded selector | **Exists** — dynamic memories are exactly this |
| A delivery mechanism | **Exists** — outbound webhooks |
| An evaluation loop connecting them | **Missing** |

See the gap section below for the proposed shape.

---

## 12 · Meeting Intelligence

> *"Transcribe Zoom/Teams recordings with decision extraction"*

| Stage | Implementation |
|-------|----------------|
| **Arrives** | A recording-complete webhook carrying a `recordingId` — **not** bytes |
| **Ingest** | `Pending` ref → **W2 fetch** resolves the recording into N assets and re-queues each as its own job: video, audio, transcript, chat |
| **Memory** | A `session` memory per meeting, with a **`continues`** link to the previous instance of a recurring series |
| **Enrichment** | Decisions and commitments into the **intent index** as structured fields — not prose to search later |
| **Cost gate** | Transcription is opt-in per producer. Media is the most expensive thing in the pipeline and must not be the default |
| **Query** | *"What did I agree to on Tuesday, and what is still open?"* |

**Why the fan-out matters:** one corrupt asset in a recording must not fail the other three. Each
becomes an independently retryable job under a depth cap and a per-root budget.

---

## The two gaps this catalog exposes

Writing twelve use cases out surfaced exactly two things the design does not do. Both are small.
Neither was visible from the five families.

### Gap 1 — retrieval was pull-only, and four use cases needed push · **closed**

Standing queries ship in two kinds, because *"tell me when this arrives"* and *"tell me when this
comes due"* cannot be one mechanism. An **arrival** rule matches each new write once and never
re-scans; a **date** rule fires when the calendar reaches a record, which no predicate over new
writes can do, because nothing arrives on the day a deadline approaches. Delivery is by poll, by
promotion into a memory, or by signed webhook through the sender the alerts already use.

The original text follows, because the reasoning is what made the shape obvious.

#### As it stood

| Use case | What it actually wants |
|----------|------------------------|
| Media monitoring (§11) | *Tell me when this brand is mentioned negatively* |
| IoT thresholds (§6) | *Tell me when the freezer goes above −18°C* |
| Legal deadlines (§7) | *Tell me thirty days before this obligation falls due* |
| Compliance retention (§5) | *Tell me when a record in class X ages past Y* |

Four of twelve, across three families. That is not a niche.

**Proposed: a standing-query worker (W10).**

```
standing_query
  selector        the same bounded selector dynamic memories use
  delivery        webhook | email | channel | memory-promotion
  scope           evaluated against new writes only
  owner           a principal — matches are ACL-filtered like any read
```

The design rule that keeps it bounded: **evaluate on the write path against newly written items
only, never re-scan the corpus.** A standing query is cheap because it sees each item once. A
standing query that re-scans is a scheduled full-table scan someone will register a hundred of.

Two consequences worth stating now:

- **Delivery is a read.** A match delivered to a principal who cannot see the item is a data leak
  through the notification channel. Standing queries are ACL-filtered at *delivery* time using the
  owner's rights at that moment, not at registration time.
- **Time-based triggers are not selectors.** *"Thirty days before a due date"* is not a predicate
  over new writes — nothing arrives on that day. Those need a scheduled sweep over date facets,
  which is W4's shape, not W10's. **Two mechanisms, not one** — conflating them produces a
  standing-query engine that quietly re-scans.

### Gap 2 — derived artifacts were hardcoded to "summary" · **closed**

`POST /memories/{id}/derive` takes a named generator: summary, study guide, flashcards,
obligations, briefing or timeline. A generator is a prompt and a name, so adding one is
configuration — and the prompt is in the fingerprint, so editing one detectably invalidates
everything it wrote.

**Deriving and compressing became two things**, which is the part worth keeping. Deriving produces
an artifact; compression is the policy of archiving the originals behind one, and only a summary is
allowed to do it. A flashcard deck that folded the course away would leave itself as the only
remaining copy of it.

#### As it stood

Covered in full at §9. `compress` becomes `derive` with a pluggable generator, reusing the existing
generator registry. Turns study guides, flashcards, obligation extracts and customer briefings into
configuration rather than four endpoints.

---

## What this changes

| Change | Size | Why now |
|--------|------|---------|
| `compress` → `derive` with a generator registry | **Small** — the registry exists | An endpoint shape is cheap to change before clients depend on it |
| W10 standing queries | **Medium** — new worker class | Four published use cases depend on it |
| Scheduled date-facet sweep (W4 extension) | Small | Legal deadlines and retention need it, and it is *not* W10 |
| Generator-pinned trend queries | Small | Sentiment charts are wrong without it |
| Span offsets in compression member lists | **Small if now, large later** | Citations break permanently once compression runs |
| Units in normalized lab facets | Small | Silently wrong range queries otherwise |

Nothing here contradicts the existing design. Five of the six are extensions of mechanisms that are
already specified; the sixth is a new worker class in an established taxonomy.

## Requirements

- **FR-UC-1** Every published use case MUST map to a stated implementation path, or be marked as
  not-yet-supported. A use case with no path is a claim, not a feature.
- **FR-UC-2** A trend or time series over a model-derived numeric field MUST be pinned to a single
  `generator_version`, or MUST mark the discontinuity where the generator changed.
- **FR-UC-3** Changing a generator that serves a trend MUST enqueue W7 reprocess for affected
  memories, or the series MUST be marked discontinuous.
- **FR-UC-4** Derived artifacts over a memory MUST be produced by a registered generator, and the
  operation MUST NOT be specialised to summarisation.
- **FR-UC-5** A compression member list MUST carry span offsets, so citations survive the
  originals being archived.
- **FR-UC-6** Normalized numeric facets MUST carry units as part of the value.
- **FR-UC-7** A model routing chain MUST be allow-listed per project and MUST fail closed when no
  permitted provider is available. It MUST NOT fall through to a provider outside the allow-list.
- **FR-UC-8** Standing queries MUST be evaluated against newly written items only, never by
  re-scanning the corpus.
- **FR-UC-9** Standing-query matches MUST be ACL-filtered at delivery time against the owner's
  rights at that moment.
- **FR-UC-10** Time-based triggers MUST run as a scheduled sweep over date facets, and MUST NOT be
  expressed as standing queries.
