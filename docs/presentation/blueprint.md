# mem-dog — Platform Blueprint

*A design document, not a description of something running.* Parts of this exist today; most is
designed and unbuilt, and several sections record defects found in the existing design rather than
features to add.

Single-file assembly of the documentation set. The
[published artifact](https://claude.ai/code/artifact/c8a9e266-fef7-4b68-b521-dceef8d0574e) is the
same material as a web page; the individual documents under [`docs/`](../README.md) are the source
of truth.

---

## TL;DR

**The architectural bet:** exactly two things are non-negotiable — a record store and the API. The
temporal graph, the credential broker, the channel gateway, the conversational agent and even the
inference layer can be absent and the system still stands, reduced.

**If you read nothing else:**

1. **Embeddings must never use the fallback chain.** Two embedding models produce incomparable
   vector spaces, so a failover silently corrupts ranking — and without a `model_id` column you
   cannot identify the affected rows afterwards.
2. **The same fallback chain crosses a legal boundary and changes who pays.** Regulated content
   routed to a third party is an undisclosed transfer; a local $0 call falling through to a paid
   provider is a bill nobody authorised. Three defects, one mechanism.
3. **Compression defeats erasure** — unless derived artifacts record their source set as a *list*
   from the first row.
4. **Some things cannot be retrofitted, and one is impossible.** "Who accessed this record in
   March?" has no answer if you were not recording in March.
5. **This system's characteristic failure is silence.** Telemetry is designed around detecting
   *absence*, not error rates.
6. **Self-hosting is table stakes, not a moat.** The defensible position is the intersection —
   connectors × memory × private.

**The plan** is a walking skeleton: prove write → store → read, then widen. Eight slices, each
ending in a test rather than a demo.

---

## Contents


**Part I · Why this exists**

- [Use Cases](#use-cases)
- [Design Principles](#design-principles)

**Part II · What the things are**

- [Memories](#memories)
- [Cases — Correlating Information Around a Subject](#cases-correlating-information-around-a-subject)
- [Normalization](#normalization)

**Part III · Getting data in**

- [The Write API](#the-write-api)
- [Ingestion Workers](#ingestion-workers)
- [Source Coverage](#source-coverage)
- [Connector Catalog](#connector-catalog)
- [Formats](#formats)
- [Direct Uploads](#direct-uploads)
- [Crawlers](#crawlers)
- [Bulk Operations](#bulk-operations)

**Part IV · Making it useful**

- [Index Construction](#index-construction)
- [Model Catalog](#model-catalog)
- [Model Routing at Bulk](#model-routing-at-bulk)
- [Versioning & Staleness](#versioning-staleness)
- [Retrieval Quality — Feedback and Conflict](#retrieval-quality-feedback-and-conflict)
- [Multi-Language](#multi-language)

**Part V · Lifecycle**

- [Deletion](#deletion)

**Part VI · Access and privacy**

- [Tenancy & Privacy](#tenancy-privacy)
- [Access Model — Principals, Sharing and Settings](#access-model-principals-sharing-and-settings)
- [Auth & Credentials](#auth-credentials)
- [Privacy Foundations](#privacy-foundations)
- [Privacy & Compliance](#privacy-compliance)

**Part VII · Interfaces and operations**

- [API Contract & Surfaces](#api-contract-surfaces)
- [Telemetry](#telemetry)
- [Token Accounting](#token-accounting)
- [Testing](#testing)
- [Technology Choices](#technology-choices)
- [Deployment Variants](#deployment-variants)

**Part VIII · The plan**

- [Roadmap](#roadmap)
- [Implementation Plan & Stack](#implementation-plan-stack)
- [Competitive Landscape](#competitive-landscape)
- [mem-dog vs Onyx: Detailed Comparison](#mem-dog-vs-onyx-detailed-comparison)
- [mem-dog vs Glean](#mem-dog-vs-glean)

---



# Part I · Why this exists

*The problem, the bet, and who owns what*


---

## Use Cases

Five families, all in scope. Supporting every family means building to the **maximum** of each
dimension rather than the average: the strictest privacy requirement applies everywhere, the
tightest latency budget governs the eager path, the largest scale target governs capacity, and
the broadest connector catalog governs coverage.

### What the indexes enable

| What the pipeline builds | What it makes possible |
|---|---|
| Vector + BM25 + hybrid | Universal search across every source at once |
| Graph + temporal facts | "Who was CEO in 2024", "what changed since January" |
| Cross-source entity resolution | One person or org view spanning CRM, email, chat and calls |
| Normalized facets | `invoices > 10k`, `open tickets by assignee` — analytics without a warehouse |
| Intent index | "What did I commit to?", meeting follow-through |
| Summary hierarchy | Briefings — "catch me up on Acme" |
| Claim index | Contradiction detection, evidence assembly |
| Timeline memory | Activity reconstruction, journaling |

### What this actually looks like

Six concrete scenarios. Each names **what makes it work** — because most of the machinery in these
documents exists for a specific reason, and this is where those reasons become visible.

---

#### 1 · Sales — "what did we actually promise Acme?"

An account manager inherits a deal. Three years of history sits across four systems and one former
colleague's mailbox.

**Sources:** Salesforce (accounts, opportunities, notes) · Gmail (the thread) · Zoom (call
recordings and transcripts) · Slack (the internal channel) · Drive (the proposal and the redlined
contract)

**What happens:** Salesforce records normalize to canonical `Organization` and `Transaction` with
an `identifiers[]` array carrying the account id. The Zoom recording fans out into video, audio,
transcript and chat — the transcript enriches like any other text. Email threads reconstruct. All
of it correlates onto one **case** by identifier join, not by hoping an LLM matches the company
name.

**Asks:** *"What pricing did we commit to?"* · *"Who at Acme raised the security objection, and
what did we say?"* · *"What's outstanding from the last call?"*

**What makes it work:** identifier-based [correlation](#cases-correlating-information-around-a-subject) rather than entity resolution ·
[fan-out](#crawlers) turning one recording into four artifacts · the
[intent index](#index-construction) surfacing commitments · **`event_time`** ordering the timeline
by when things happened, not when a backfill ran

---

#### 2 · Company knowledge — "why did we decide this?"

A new engineer asks why the service is structured a particular way. The answer is in a design doc,
a Slack thread that changed it, and a decision nobody wrote down except in a meeting.

**Sources:** Drive and Google Docs · Slack · GitHub PR discussions · Notion · Zoom transcripts

**Asks:** *"Why did we move off the old queue?"* · *"What was decided about retention?"* ·
*"Who owns this service?"*

**What makes it work:** the temporal graph answering *what was true when* rather
than only what is true now · **[conflict surfacing](#retrieval-quality-feedback-and-conflict)** — when the design doc
and the later thread disagree, both are shown rather than one silently winning · Google Docs via
the **`export`** verb, since they have no bytes to download · citations pointing at the original
so the reader can check

---

#### 3 · Meetings — "what did I commit to?"

Six calls a day, and the follow-through lives in whichever transcript nobody re-reads.

**Sources:** Zoom, Google Meet, Teams — recordings and transcripts

**What happens:** each recording fans out; the transcript routes to the communication agent with
diarization, so *who said it* survives. Decisions, action items and commitments extract as
first-class fields, not prose.

**Asks:** *"What did I agree to this week?"* · *"What's still open from the Tuesday sync?"* ·
*"Did anyone commit to a date?"*

**What makes it work:** the [intent index](#index-construction) — decisions and commitments as
structured output rather than something to search prose for · **[memories](#memories)** grouping
a recurring meeting series, with the `session` type compressing older instances while keeping the
extracted commitments · media transcription, which is the **largest cost exposure of any format
group** and is gated behind explicit opt-in for that reason

---

#### 4 · Support — "has anyone seen this before?"

A ticket arrives describing a failure mode. Somebody solved it eighteen months ago.

**Sources:** Zendesk or Jira · internal docs · Slack engineering channels · past resolved tickets

**Asks:** *"Has this happened before?"* · *"What fixed it?"* · *"Which customers are affected by
this bug?"*

**What makes it work:** the **[question index](#index-construction)** — the ticket says *"it just
spins forever after they hit save"* and the engineer searches *"performance regression"*, which
appears nowhere in the text · [normalized facets](#normalization) making *"open tickets
mentioning this component"* a filter rather than a semantic guess · similar-case retrieval over
case-level embeddings

---

#### 5 · Clinical — a patient timeline

Encounters, labs, imaging reports and notes accumulate across systems and years.

**Sources:** EHR exports · lab systems · imaging reports (often scanned PDFs) · clinical notes ·
scheduling

**What happens:** records correlate onto a patient **case** by MRN — a deterministic join.
Scanned reports take the OCR path, which is a different pipeline and a different cost profile from
digital PDFs. Everything orders by **`event_time`**: when the lab was drawn, not when it was
imported.

**Asks:** *"Summarise this patient's cardiac history"* · *"What was the creatinine trend?"* ·
*"What medications were active in March 2024?"*

**What makes it work:** [cases declared, never inferred](#cases-correlating-information-around-a-subject) — **two patients with the same
name must never merge**, which is exactly what entity resolution would do · `event_time` ordering,
because a backfilled 2019 X-ray appearing after this week's lab is a timeline that renders
perfectly and misleads clinically · [break-glass](#access-model-principals-sharing-and-settings) with justification and
audit · **[classification-gated inference](#privacy-compliance)** pinning PHI to local models
and failing closed rather than falling through to a third party

> **Constraint worth stating.** The pipeline ships a Medical/DICOM agent, so HIPAA is in scope.
> In a hosted deployment that needs a BAA with every sub-processor touching PHI — including
> inference providers. **This is currently a self-hosted-only story** unless the cloud inference
> choice changes.

---

#### 6 · Legal — a matter

Filings, correspondence, discovery documents and privileged internal analysis, over years.

**Sources:** document management · email · e-discovery exports (often multi-GB `mbox` or archives)

**Asks:** *"What's our exposure on the indemnity clause?"* · *"When did we first learn about this?"*
· *"Which documents mention the March meeting?"*

**What makes it work:** **asserted vs inferred** membership — in a legal context *"this document is
in the matter"* and *"this document appears related"* are categorically different claims, and a
system that collapses them is useless for either purpose · **[ethical walls](#access-model-principals-sharing-and-settings)**
as case-level deny lists that survive membership changes · **[legal hold](#deletion)**
blocking erasure and returning partial completion naming what was withheld · streaming `mbox`
parsing, since a discovery export is one multi-gigabyte file containing fifty thousand messages

---

#### The pattern across all six

Every scenario is the same shape: **heterogeneous sources, one subject, a question that spans
them.** The domain changes what the sources are and how strict the privacy is. The machinery does
not.

That is the argument for building the intersection rather than six vertical products — and it is
also why the correlation, provenance and privacy work is not optional infrastructure. Take any of
it out and the scenarios stop working in ways nobody notices until someone acts on a wrong answer.

### The five families

| Family | Covers | Needs |
|--------|--------|-------|
| **A · Personal memory** | Universal search, ask-your-life Q&A with citations, timeline reconstruction, contact recall, commitment tracking; reachable from any messaging app | Breadth of connectors, cheap local inference |
| **B · Team memory** | Shared knowledge base, onboarding, meeting intelligence, customer 360, institutional memory, cross-tool reporting | Per-item privacy, deep connectors |
| **C · Agent infrastructure** | Memory layer for other AI — MCP tools, persistent context across sessions, RAG backend | Sub-second retrieval, composable primitives |
| **D · Embedded backend** | Private memory behind someone else's product — workspaces, scoped keys, project-isolated retrieval | Tenancy contracts, quotas, purge and export |
| **E · Governance** | Data inventory, erasure and DSAR execution, audit trail | Falls out of the architecture — *if designed early* |

### The tensions, and how each resolves

| Tension | Resolution |
|---------|------------|
| **Personal and team want opposite privacy defaults.** A wants everything indexed and surfaced; B must guarantee no teammate sees your personal mail. | **New mechanism** — data inherits its ACL from the *connection* that produced it, not the space it lands in. |
| **Product vs platform RBAC.** B needs mem-dog to enforce; D delegates to the host. | **Unify** — one enforcement path. The host model becomes the case where a service identity is a single broad principal. |
| **Breadth vs depth.** A wants 300 shallow connectors; B's customer 360 needs three handled deeply. | Already solved by the tier model — Tier 3 for breadth, Tier 1 for depth. An ordering, not a contradiction. |
| **Cost profiles diverge.** A on local models is $0; D at 1k workspaces is a firehose. | Configuration — per-org budget caps plus tier policy. |
| **Latency expectations diverge.** C needs sub-second in-loop; B tolerates ten seconds. | Already solved by the eager / deferred / adaptive index split. |

#### The unifying rule

**ACL inheritance follows the connection, not the container.**

A connection carries a scope — `personal` or `shared` — set at connect time. Personal Gmail
inside a team org produces `private` items regardless of project defaults; a team Slack connected
as `shared` produces member-visible items. Same org, same pipeline, opposite defaults, no
contradiction.

It also closes the proxy authorization hole: permission becomes "owns this connection, or it is
shared" rather than "authenticated to the org".

### What supporting everything rules out

1. **No single global privacy default** — visibility must be connection-scoped and space-aware.
2. **No fixed five-mode retrieval API** — Family C requires composable primitives.
3. **No host-delegated-only RBAC** — mem-dog must enforce natively.
4. **The delete cascade cannot be deferred** — governance is table stakes for B and D.
5. **The global unscoped `API_KEY` must go** — it voids every ACL the other families depend on.

### Build order

Build what the most families share first. Required by every family:

```
content contract → worker split → identity abstraction and auth →
connection-scoped ACL model → tenancy scoping → W7 reprocess
```

Then roughly: **A** (breadth, tier-3 default path) → **D** (embedded) → **C** (agent, composable
retrieval) → **B** (team, sharing surface and deep connectors).

**One deliberate inversion:** build **E** early despite ranking it last in urgency. The delete
cascade is cheap to design now and expensive to retrofit once compression has baked content into
summaries in production.

### Where the differentiation sits

| Capability | Position |
|---|---|
| Universal search | commodity |
| RAG backend | commodity — mem0, Zep, LlamaIndex |
| Temporal knowledge graph | contested — Zep owns the engine mem-dog runs |
| Self-hosted, air-gapped, $0 | **table stakes** — Onyx does it under MIT |
| **Messaging-channel ingestion** | **genuinely unique** |
| **Connectors × memory × private** | the wedge — the intersection is unoccupied |

See competition/ for the full analysis.


---

## Design Principles

### The central bet

Reading the system end to end, exactly two **roles** are non-negotiable: a **record store** and
the **API surface**. Every other capability — the temporal graph, the credential broker, the
channel gateway, the conversational agent, the inference layer — can be absent without taking the
system down.

That single property drives the deployment story, the fallback chains, and the phasing of
everything else.

> Stated as roles rather than products deliberately. Which technology fills each role is an
> implementation choice, recorded in [operations/technology.md](#technology-choices).

### Dependency criticality

| Role | Status | Degradation if absent |
|------|--------|----------------------|
| `record store` | **required** | Total. Durable, queryable home for data, memories, embeddings, graph layer 1 and tenancy |
| `API surface` | **required** | Total. Sole writer of record — all tenancy and ACL enforcement lives here |
| `blob store` | prod | Raw binary ingestion unavailable; text paths continue |
| `durable queue` | enrich | Ingest still succeeds; items land unenriched until drained |
| `inference layer` | enrich | Deterministic classification survives; no analysis, embeddings or chat |
| `temporal graph store` | optional | Lose temporal facts, point-in-time queries, graph and full retrieval modes |
| `credential broker` | optional | Lose OAuth integrations and the credential-injecting proxy |
| `channel gateway` | optional | Lose channel ingestion. API, SDK, MCP and UI unaffected |
| `conversational agent` | optional | Lose messaging-app access to the corpus |
| `tool server` | optional | Lose MCP-client integration |

### Cross-cutting invariants

| Invariant | Enforced where |
|-----------|----------------|
| **Every query is scoped by `user_id`** | API storage layer, unconditionally |
| **Integration credentials never travel** | Workers receive references; injection happens at a proxy |
| **Deterministic before probabilistic** | Six heuristic layers precede any LLM classification call; ~80% never reach an LLM |
| **Degrade, don't fail** | Every AI dependency has an ordered fallback chain — with two exceptions (embeddings, regulated content) where fallback is unsafe |
| **One writer of record** | All persistence flows through the API so access control is enforced in one place |
| **Additive configuration** | New fields default safely; new scoping is optional. Upgrades need no migration |
| **Runtime config beats redeployment** | Routing, agent configs, schemas and crawler configs live in the database and take effect on the next invocation |
| **Provenance must not be trustable** | Agents branch on a closed content contract, never on caller-supplied fields |
| **Derived artifacts inherit the strictest source ACL** | Filtering happens in the query, never post-rank |
| **IDs are ULID + type prefix** | All create paths |

### Capability ownership

`●` owns · `○` participates · `—` not involved.
**GW** gateway · **API** core · **PIPE** pipeline · **UI** web app · **DM** conversational agent ·
**MCP** tool server.

| Capability | GW | API | PIPE | UI | DM | MCP | Requires |
|-----------|:--:|:---:|:----:|:--:|:--:|:---:|----------|
| Inbound webhook receipt | ● | — | — | — | — | — | record store |
| Identity resolution | ● | — | — | — | ● | — | record store |
| Envelope normalisation | ● | — | — | — | — | — | — |
| Integration relevance tagging | ● | — | — | — | — | — | credential broker |
| Direct data write | — | ● | — | ○ | ○ | ○ | record + blob store |
| Versioning & diffs | — | ● | — | ○ | — | — | record store |
| Authentication | ○ | ● | — | ○ | ○ | ○ | record store |
| Per-item access control | — | ● | — | — | — | ○ | record store |
| Org / project scoping | — | ● | — | ● | — | — | record store |
| Durable ingest handoff | — | ● | ● | — | — | — | durable queue |
| Classification cascade | — | — | ● | — | — | — | inference (last resort) |
| Typed sub-agent analysis | — | — | ● | ○ | — | — | inference |
| Per-agent configuration | — | ● | ● | ● | — | — | record store |
| Embedding generation | — | ○ | ● | — | — | — | inference + vector index |
| Entity extraction | — | ○ | ● | — | — | — | inference |
| Relational graph write | — | ● | ○ | — | — | — | record store |
| Temporal graph write | — | ● | ○ | — | — | — | graph store |
| **Crawl discovery** | — | ○ | ● | ● | — | — | credential broker |
| Vector search | — | ● | — | ○ | ○ | ○ | vector index |
| Lexical search | — | ● | — | ○ | ○ | ○ | lexical index |
| Graph & temporal search | — | ● | — | ○ | ○ | ○ | graph store |
| Reranking | — | ● | — | ○ | — | — | inference |
| RAG answer with citations | — | ● | — | ○ | ○ | ○ | inference |
| Memory TTL & expiry | — | ● | — | ○ | — | ○ | record store |
| Memory compression | — | ● | ○ | ○ | — | — | inference + record store |
| OAuth & token refresh | — | ○ | — | ● | — | — | credential broker |
| Credential-injecting proxy | ● | — | — | — | — | — | credential broker |
| Model tiers & fallback | — | ● | ● | ● | — | — | record store + inference |
| Provider key encryption | — | ● | — | ○ | — | — | record store |
| Managed pod lifecycle | — | ● | — | ● | — | — | orchestrator |
| Conversational channel agent | — | ○ | — | — | ● | — | inference |
| MCP tool surface | — | ○ | — | — | — | ● | — |
| Tracing & token accounting | ○ | ● | ○ | ● | ○ | ○ | record store |



# Part II · What the things are

*The data model everything else operates on*


---

## Memories

A memory is a **typed container with a lifecycle**. Data items are mapped into memories; the type
decides how long they live, whether they compress, and what happens when they expire.

This is the axis on which the product differs most from adjacent memory layers — those offer three
or four fixed scopes. Here the set is configurable, which only works if the lifecycle semantics are
specified rather than implied.

> Not to be confused with a [case](#cases-correlating-information-around-a-subject). A memory is a **lifecycle** container — it answers
> "how long does this matter?". A case is a **subject** — it answers "what is this about?". A
> conversation expires; a patient does not.

### A type is a name, a TTL, and what happens at the end

Deliberately three fields. Not a taxonomy.

```
MemoryType
  name        conversation | session | factual | <anything>
  ttl         duration, or null for never
  on_expiry   orphan_delete | keep_members | archive
```

Earlier drafts shipped ten types across four categories with semantics baked into each. That is a
lot of product opinion to impose, and most of it is expressible as *a TTL and an expiry policy* —
so the categories are gone and the set is open.

The system ships a few sensible ones and organisations add their own:

| Shipped | TTL | `on_expiry` |
|---------|-----|-------------|
| `default` | never | — |
| `conversation` | 1 hour | `orphan_delete` |
| `session` | 24 hours | `archive` |
| `tracing` | 3 days | `orphan_delete` |

Everything else is a type someone defines — commonly `factual`, `episodic`, `semantic`,
`organizational`, and **`procedural`**.

`procedural` is worth naming because the taxonomy borrows episodic and semantic from cognitive
science and the third member is the one an agent system most needs: *how we do X here* — runbooks,
workflows, learned procedures. It fits neither `semantic` (concepts) nor `factual` (assertions). Precedence for definitions is the usual
project → org → shipped, with admin locks.

**`default` exists so nothing is orphaned.** An item written with no memory and no matching routing
rule lands there. Without it, unattached data is invisible from the memory side entirely — which
would leave a hole in exactly the view memories are for.

#### Type is mutable, so it is not in the identifier

```
mem_<ulid>            not  mem_<type>_<ulid>
```

If a memory can change type, an identifier encoding the type becomes a lie the moment it does. The
type is a field.

### Memories move between types

This is what makes three fields enough. You do not need ten types if a memory can be re-typed.

A conversation that turns out to contain durable facts is **promoted** rather than expiring. A
working set that has gone cold is **demoted** rather than being deleted by hand.

```http
PATCH /api/v1/memories/{id}   { "type": "factual" }
```

The TTL is recomputed from the new type, which is where the care is needed:

| Direction | Effect | Handling |
|-----------|--------|----------|
| To a longer or null TTL | Expiry is cancelled or pushed out | Safe — apply immediately |
| **To a shorter TTL** | May be **already expired** under the new type | **Preview before applying** — say what will be deleted, as with any destructive operation |

Bulk re-typing takes a selector and runs as a job, on the same run entity as bulk import and
deletion. Same dry-run, same per-item results.

Automatic promotion — rules that re-type a memory when it accumulates enough durable content — is
a later addition. It is the same shape as crawler routing: an agent may **propose** a promotion; a
rule or a person applies it.

### Membership is many-to-many

A single message legitimately belongs to a `conversation` memory (one hour), a `timeline` memory
(seven days), and may contribute to a `user` memory that never expires.

```
memory_members
  memory_id, data_id
  added_at
  added_by     explicit | routed | agent
```

`added_by` matters for the same reason it does on case membership: "this item is in this memory
because the caller said so" and "because a rule put it there" are different claims, and only one of
them should be silently re-evaluated when the rule changes.

### Memories are unique, and memories correlate

Two properties that depend on each other: **you cannot reliably correlate to something that
duplicates.**

#### Uniqueness — a natural key

A memory carries an optional `memory_key`, unique within `(project, type)`:

```
memory
  memory_id    mem_<ulid>            surrogate, stable forever
  type         mutable                type is a field, not part of the id
  memory_key   natural key           unique per (project, type)
```

Writes **upsert** on it. Messages sharing a thread id land in the same `conversation` memory on
every write, from every producer, forever — rather than accumulating a new memory per batch,
per restart, or per client that forgot it had one.

Same pattern as `external_id` on data items and cases. Third use, one idea: **a caller-supplied
natural key, an internal surrogate, and upsert between them.**

Without it, routing creates a new memory whenever the router restarts, correlation points at
whichever duplicate happened to be current, and TTL expires a fragment of a conversation while the
rest lives on.

#### Correlation — memories form a graph

Memories are not isolated containers. A conversation belongs to a user. A session is part of a
longer timeline. A summary is derived from the conversations it compressed. A support thread is
*about* a case.

```
memory_links
  from_memory_id, to_memory_id
  relation      part_of | derived_from | about | continues | supersedes
  created_by    explicit | routed | agent
  confidence    for derived links
```

| Relation | Example |
|----------|---------|
| `part_of` | A session inside a timeline |
| `derived_from` | A compressed summary and the conversations it replaced |
| `about` | A conversation and the [case](#cases-correlating-information-around-a-subject) it concerns |
| `continues` | Today's session resuming yesterday's |
| `supersedes` | A corrected memory replacing an earlier one |

#### Declared correlation and derived correlation are different claims

The same distinction that governs [case membership](#cases-correlating-information-around-a-subject), for the same reason:

| | **Declared** | **Derived** |
|---|---|---|
| Source | A caller or a routing rule said so | Shared members, shared entities, temporal proximity |
| Authority | Authoritative | **Suggestive** |
| Use | Traversal, expiry policy, access decisions | Ranking, "related to this", discovery |
| Reversal | Explicit unlink | Recomputed whenever the signal changes |

**Derived correlation must never drive an access or lifecycle decision.** Two memories sharing
eleven data items are probably related; that is a good reason to surface one while reading the
other, and a bad reason to extend one's TTL because the other was touched.

#### What correlation unlocks

- **Traversal at retrieval** — answering from a conversation and the case it is about, in one query
- **Compression lineage** — a summary that knows what it replaced, which is what makes the
  originals recoverable and the erasure cascade possible
- **Expiry that respects structure** — a session `part_of` a live timeline is not silently orphaned
- **"What else is like this"** — derived correlation as a retrieval signal rather than a link

### How data gets mapped at write time

Three ways, in precedence order:

| Mechanism | Example |
|-----------|---------|
| **Explicit** — the write names a memory | A client that manages its own sessions |
| **Producer default** — the producer declares a target type | A chat channel producer writes into `conversation` |
| **Routing rule** — key-based grouping, auto-creating the memory | Messages sharing a thread id collect into one conversation memory |

The routing rule is what makes conversation memory work without every caller tracking session
state. It is also the one that needs a cap: an unbounded key space creates unbounded memories.

### Membership is mutable — memories are formed, not just routed

Routing at write time is the convenience. The primitive is that **any item can be added to or
removed from any memory at any time**, so memories are formed after the fact as readily as during
ingestion.

```http
POST   /api/v1/memories/{id}/members      add — item ids, or a selector
DELETE /api/v1/memories/{id}/members/{data_id}
POST   /api/v1/memories/{id}/members:bulk selector-based add or remove, as a job
```

Adding by **selector** is what makes "create a memory dynamically" a real operation rather than a
loop: create a memory, point it at everything tagged `incident-4471` from last Tuesday, and it is
populated.

#### Remove is not delete

Removing a member **unmaps** it. The data item is untouched — it keeps its other memberships, its
embeddings, its entities and its place in retrieval.

Deleting is a [different operation](#deletion) with a different endpoint and a
cascade. Conflating them is how someone tidies a memory and loses data, so they are not the same
verb and the API does not let them be confused.

#### Nothing becomes orphaned

Removing an item's **last** membership moves it to the project's `default` memory rather than
leaving it unattached. The invariant holds: every item is in at least one memory, so the memory
view is always complete and "which memories hold this?" always has an answer.

#### Static and dynamic membership

Two kinds of memory, and the second is where "dynamically" earns its name:

| | **Static** | **Dynamic** |
|---|---|---|
| Defined by | An explicit member list | A **selector**, re-evaluated |
| Changes when | Someone adds or removes | The underlying data changes |
| Suits | A curated set, an incident, a reading list | "Everything tagged urgent", "this month's invoices" |
| Membership provenance | `explicit` | `routed` |

A dynamic memory is a saved selector with a lifecycle attached. Its members change without anyone
touching it — which is powerful and needs two guards:

- **TTL applies to the container, not to computed membership.** A dynamic memory with a one-hour
  TTL expires the *memory*; `orphan_delete` then only reaches items nothing else holds
- **Selector evaluation is bounded**, exactly as crawler scope is. An unconstrained selector on a
  large corpus is an expensive query someone will schedule

#### Membership changes are a staleness trigger

A memory-level artifact — a summary, a compression, a memory-scoped embedding — is derived from its
member set. **Adding or removing a member marks those artifacts stale**, exactly as it does for
[case artifacts](#cases-correlating-information-around-a-subject).

Which is the same machinery again: the artifact records its member set as a list, the list changed,
reprocess rebuilds. Nothing new to build.

### Expiry is deletion, and needs reference counting

**This is the part that goes wrong if it is treated as a cleanup job.**

A `conversation` memory expires after an hour. Its members include a message that is *also* in a
`factual` memory that never expires. Deleting the conversation's members deletes data the factual
memory still depends on — and nothing errors, because from the conversation's point of view the
operation succeeded.

Exactly the shape of the entity problem in [deletion](#deletion): remove the
*contribution*, not the thing.

So `on_expiry` is a policy per type:

| Policy | Behaviour | Suits |
|--------|-----------|-------|
| **`orphan_delete`** | Remove membership; delete the data item **only if no other memory holds it** | `conversation`, `session` — ephemeral context |
| `keep_members` | Remove the memory, leave the data | `timeline` — the container was a view, not an owner |
| `archive` | Compress into a summary, archive originals | `session` at threshold, per the compression policy |

**Default to `orphan_delete`, never to unconditional delete.** The failure mode of the conditional
version is retaining slightly more than necessary. The failure mode of the unconditional version is
silent data loss from a container the user did not think of as owning anything.

Expiry runs through the same cascade as any other deletion — chunks, embeddings, blobs, entity
contributions, summaries marked stale — because an expiring memory that leaves orphaned embeddings
behind is a slow leak that only shows up as a storage bill.

### Effective TTL is computed, not stored

Many-to-many membership plus mutable membership has a consequence worth stating explicitly:

> **An item's effective TTL is the maximum across all the memories that hold it — and it changes as
> membership changes.**

An item in a one-hour `conversation` that is then added to a permanent `factual` memory has just
become permanent. Remove it from the factual memory and it becomes deletable again, subject to
whatever else still holds it.

That is correct behaviour. The problem is that it makes "when does this expire?" **not a property
of the item**. There is no `expires_at` column that is true, because the answer is derived from a
set that changes.

#### So expose the computation, do not store the answer

| Anti-pattern | Why it fails |
|--------------|-------------|
| An `expires_at` column on the item | Wrong the moment any membership changes, and nothing recomputes it reliably |
| Earliest membership TTL | Deletes data a permanent memory still depends on — the `orphan_delete` bug in another form |
| Nothing at all | "Why did this vanish?" and "why is this still here?" become unanswerable |

The reverse lookup carries it:

```http
GET /api/v1/data/{id}/memories
```

```json
{ "effective_expiry": null,
  "reason": "held by a memory with no TTL",
  "memberships": [
    { "memory_id": "mem_01J…", "type": "conversation", "added_by": "routed",
      "expires_at": "2026-08-27T11:04:00Z" },
    { "memory_id": "mem_01J…", "type": "factual", "added_by": "explicit",
      "expires_at": null }
  ] }
```

`effective_expiry` with the **reason** is the whole point. "Held by a memory with no TTL" answers
the question in one read; a list of timestamps the caller has to reduce does not.

The expiry sweeper computes the same thing rather than reading a column — which is what makes
`orphan_delete` correct by construction instead of by remembering to check.

#### Removing a membership can make something deletable

Worth surfacing in the UI, because it is the one non-obvious destructive side effect in the whole
memory model: unmapping an item from the memory that was keeping it alive schedules its deletion.

The reverse-lookup view should say so before the removal, not after.

### Compression, and what it does to membership

Compression summarises a memory's members into one artifact and archives the originals. Two
consequences already designed elsewhere, restated because they meet here:

- The summary records its **member set as a list**, which is what makes a later erasure of one
  member possible ([privacy foundations](#privacy-foundations))
- Archived originals remain searchable with `include_archived=true`, so compression is a retrieval
  default rather than a deletion

### Seeing the mapping

Both directions are needed, and only one of them is obvious:

| Query | Answers |
|-------|---------|
| `GET /api/v1/memories/{id}/members` | What is in this memory — with each item's `added_by` and the memory's remaining TTL |
| **`GET /api/v1/data/{id}/memories`** | **Which memories hold this item** — the one that explains why something did or did not expire |

The second is the diagnostic that matters. "Why is this still here?" and "why did this vanish?" are
both answered by the membership list, and neither is answerable from the memory side alone.

The write response should carry it too: an item written into a routed conversation memory should
say so, rather than leaving the caller to discover the mapping by querying.

### API

```
CRUD  /api/v1/memory-types                 typed config, per scope, with lock state
CRUD  /api/v1/memories
GET   /api/v1/memories/{id}/members
POST  /api/v1/memories/{id}/members        explicit mapping
GET   /api/v1/data/{id}/memories           reverse lookup
POST  /api/v1/memories/{id}/compress
PATCH /api/v1/memories/{id}                ttl_hours, no_expiry
```

Everything is available in the UI at the same granularity — memory-type editor with TTL and expiry
policy, a memory browser showing members and time remaining, and the reverse lookup on any item.

### Requirements

- **FR-MEMT-1** Memory types MUST be configurable, not hardcoded, with precedence
  project → org → shipped default and admin locks.
- **FR-MEMT-2** The system MUST ship a small working set including a `default` type, so that an
  item written with no memory is never orphaned and no configuration is required to store one.
- **FR-MEMT-3** A memory type MUST be definable as a name, a TTL and an expiry policy. Richer
  attributes MAY be added later but MUST NOT be required.
- **FR-MEMT-4** Membership MUST be many-to-many, and MUST record whether it was explicit, routed or
  agent-assigned.
- **FR-MEMT-5** Expiry MUST NOT delete a data item that another memory still holds. The default
  policy MUST be `orphan_delete`, never unconditional deletion.
- **FR-MEMT-6** Expiry MUST run the full deletion cascade, so no orphaned embeddings, chunks or
  blobs survive it.
- **FR-MEMT-7** The API MUST expose both directions of the mapping — the members of a memory, and
  the memories holding an item.
- **FR-MEMT-8** A write response MUST report which memories the item was mapped into.
- **FR-MEMT-9** Routing rules MUST be bounded, so an unbounded key space cannot create unbounded
  memories.
- **FR-MEMT-10** A memory MUST support an optional natural key, unique within (project, type), and
  writes MUST upsert on it.
- **FR-MEMT-11** Memories MUST support typed relationships to other memories and to cases.
- **FR-MEMT-12** Correlation MUST record whether it was declared or derived, and **derived
  correlation MUST NOT drive access or lifecycle decisions**.
- **FR-MEMT-10** A memory MUST support an optional natural key, unique within (project, type), and
  writes MUST upsert on it.
- **FR-MEMT-11** Memories MUST support typed relationships to other memories and to cases.
- **FR-MEMT-12** Correlation MUST record whether it was declared or derived, and **derived
  correlation MUST NOT drive access or lifecycle decisions**.
- **FR-MEMT-13** A memory's type MUST be mutable, and the identifier MUST NOT encode it.
- **FR-MEMT-14** Re-typing MUST recompute TTL. Moving to a shorter TTL MUST preview what would be
  deleted before applying.
- **FR-MEMT-15** Bulk re-typing MUST run as a job with dry-run and per-item results.
- **FR-MEMT-16** Membership MUST be mutable after write — items addable and removable individually
  and by selector, the latter as a job.
- **FR-MEMT-17** Removing a member MUST NOT delete the data item; unmapping and deletion MUST be
  distinct operations.
- **FR-MEMT-18** Removing an item's last membership MUST place it in the project's `default`
  memory. No item may be left unattached.
- **FR-MEMT-19** Memories MUST support dynamic membership defined by a bounded selector, and
  dynamic membership MUST be recorded as `routed` rather than `explicit`.
- **FR-MEMT-20** A membership change MUST mark memory-level derived artifacts stale.
- **FR-MEMT-21** An item's effective expiry MUST be computed as the **maximum** TTL across its
  memberships. It MUST NOT be stored as a column on the item.
- **FR-MEMT-22** The reverse lookup MUST return the effective expiry together with the reason for
  it and the per-membership detail.
- **FR-MEMT-23** Removing a membership that would make an item deletable MUST surface that effect
  before the removal is applied.


---

## Cases — Correlating Information Around a Subject

A patient timeline. All telemetry from one machine. A legal matter. Each is the same shape: **a
long-lived subject that accumulates heterogeneous data from many sources over time, and must be
retrieved and reasoned about as a unit.**

Nothing in the current model expresses that.

### Why none of the existing primitives work

| Candidate | Why not |
|-----------|---------|
| **Project** | Designed as a workspace; the hierarchy is org → project. A hospital is not 100,000 projects, and the capacity plan targets ~1,000 |
| **[Memory](#memories)** | Closest fit — it already contains data items — but memory types are *lifecycle* concepts (TTL, expiry, compression). No stable external identifier, no typed attributes, no membership provenance |
| **Tags** | No identity, no attributes, no access control, no lifecycle |
| **Graph entity** | **The dangerous one.** Entities are *extracted*, and entity resolution actively tries to merge similar ones. Two patients with the same name must never merge. Authoritative subjects cannot be probabilistic |

That last row is the important one. The graph is the obvious place to reach for and precisely the
wrong one: everything that makes entity resolution useful for knowledge extraction makes it unsafe
for identity.

**A case is declared, not inferred.**

### The primitive

```
Case
  case_id            cas_<ulid>
  external_id        caller-supplied, stable, unique per (project, case_type)
  case_type          patient | matter | asset | incident | <custom>
  attributes         typed, schema-validated per case_type
  identifiers[]      MRN, docket number, serial number, VIN …
  status             open | closed | archived
  acl                its own — not inherited from the project
  retention          policy, including legal hold
```

Membership is separate and carries provenance:

```
CaseMember
  case_id, data_id
  member_type        asserted | inferred     ← must be distinguishable
  confidence         for inferred members
  event_time         when the thing happened, not when we ingested it
  added_by, added_at
```

#### Two fields that carry most of the weight

**`member_type`.** In a legal or clinical context there is a categorical difference between *this
document is in the case* and *this document appears related to the case*. Collapsing them produces
a system nobody can rely on for either purpose. Inferred members should be reviewable and
promotable to asserted.

**`event_time`.** See below — this is the one that breaks silently.

### Correlation: four mechanisms, in order of preference

| Mechanism | Deterministic? | Example |
|-----------|:--------------:|---------|
| **Explicit** — caller supplies `case_id` at write | yes | The host system already knows the patient |
| **Identifier match** — normalized `identifiers[]` field | **yes** | MRN `A12345` in a lab result matches the patient's MRN |
| Entity link — via the graph | no | A person entity connects to the case's subject |
| Proximity — time window plus partial identifier | no | Suggested only, always `inferred` |

The second mechanism is where **normalization pays off enormously**. If records normalize to
canonical types carrying an `identifiers[]` array, correlation becomes a **join on an identifier**
rather than an LLM inference. Deterministic before probabilistic, one layer up again.

This makes `identifiers[]` a required canonical field, not an optional one — a change to
[ingestion/normalization.md](#normalization).

### Event time is not ingestion time

A timeline ordered by ingestion time is not merely imprecise — it is **wrong in a way that looks
right**.

Consider backfilling three years of patient history. Every record was ingested this morning. Order
by `created_at` and the 2019 chest X-ray appears after this week's lab result. The timeline renders,
looks plausible, and is clinically misleading.

The crawler work makes this acute: **historical import is now a first-class path**, and historical
import is exactly what scrambles ingestion-ordered timelines.

So:

- `event_time` becomes a **first-class normalized field**, extracted per source
- Timelines order by `event_time`, falling back to ingestion time only when it is genuinely absent
- Items with no event time are **visibly marked**, not silently placed
- Both times are retained — "when did we learn this" is a real question, especially for audit

This is the transaction-time/valid-time distinction from
[retrieval/versioning.md](#versioning-staleness), arriving from a different direction.

### Case-level derived artifacts

A case is not just a bag of items. It accumulates its own derived layer:

| Artifact | Purpose |
|----------|---------|
| **Rolling summary** | "Catch me up on this matter" without reading 400 documents |
| **Timeline** | Ordered events with source attribution |
| **Key facts** | Case-level structured state — diagnosis, claim value, machine model |
| **Contradiction set** | Where members disagree — clinically and legally significant |
| **Case embedding** | Embed the summary, so **"find similar cases"** works — precedent search, similar presentations |

That last one is a genuine capability rather than a nicety, and it falls out almost free once a
case summary exists.

All of these are derived artifacts, which means the staleness model applies — and adds a new
trigger: **adding or removing a member invalidates case-level artifacts.** A case that gained a
document yesterday has a stale summary today. See [retrieval/versioning.md](#versioning-staleness).

### Access control

Case ACL is **not** project ACL, and this is the part most likely to be got wrong.

A hospital project contains every patient; access is per-patient or per-care-team. A firm's project
contains every matter; access is per-matter, and ethical walls exist specifically to prevent
cross-matter visibility.

```
effective access = most restrictive of ( item ACL , case ACL )
```

Item ACL still derives from the connection that produced it
([use-cases.md](#use-cases)); the case then constrains further. A case can never *widen* access
to an item.

Two additions the domains demand:

- **Break-glass** — emergency override with mandatory justification and an immutable audit record.
  A clinical system without it is unsafe; one without the audit trail is unaccountable.
- **Ethical walls** — explicit deny lists at case level, which must survive membership changes.

### Retention, and a conflict worth surfacing

Cases carry retention policy, and one setting collides with something already designed:

> **A case under legal hold must not be deleted, including on an erasure request.**

That directly conflicts with the delete cascade in
[security/compliance.md](#privacy-compliance). The resolution is that legal obligation
generally prevails — but the system must **represent the conflict explicitly** rather than resolve
it silently in either direction:

- An erasure request touching a held case returns a **partial completion** naming what was
  withheld and why
- The hold, its scope and its authority are recorded and auditable
- Release of the hold **re-queues** the deferred erasure

Silently deleting held data and silently ignoring an erasure request are both serious failures.
The only defensible behaviour is to do what is possible and say precisely what was not.

### API

Designed for a host system that already owns the subject.

```http
PUT  /api/v1/cases                         # upsert by external_id — idempotent
GET  /api/v1/cases/{id}
GET  /api/v1/cases?identifier=MRN:A12345   # resolve by identifier
POST /api/v1/cases/{id}/members            # assert membership
GET  /api/v1/cases/{id}/timeline           # ordered by event_time
GET  /api/v1/cases/{id}/summary            # derived, with staleness state
POST /api/v1/cases/{id}/retrieve           # case-scoped retrieval
POST /api/v1/cases/similar                 # case-level embedding search
```

Writes accept membership inline, so a host does not need two round trips:

```json
POST /api/v1/write
{
  "producer_id": "key_01JQRS...",
  "items": [{
    "content": { "kind": "inline", "text": "..." },
    "case": { "external_id": "MRN-A12345", "case_type": "patient" },
    "event_time": "2019-03-14T09:20:00Z"
  }]
}
```

`PUT` on cases rather than `POST`, keyed by `external_id`, because the host system's identifier is
the source of truth and re-sending it must not create a second case.

Bulk writes carry case assignment per item — see
[operations/bulk-operations.md](#bulk-operations).

### What changes elsewhere

| Area | Change |
|------|--------|
| **Normalization** | `identifiers[]` and `event_time` become required canonical fields |
| **Ingestion** | `case` accepted on write; correlation rules evaluated; bulk writes carry per-item case assignment |
| **Retrieval** | `case_id` becomes a filter dimension; case-scoped RAG; case-level embeddings for similarity |
| **Indexes** | Case summary, timeline and contradiction set join the index set |
| **Versioning** | Membership change becomes a staleness trigger for case artifacts |
| **Access control** | Effective ACL is the intersection of item and case; break-glass; ethical walls |
| **Compliance** | Per-case retention, legal hold, per-case access audit |
| **Telemetry** | Case size distribution, orphan items, inferred-membership confidence, cases with stale summaries |
| **API** | Case CRUD, membership, timeline, case-scoped retrieve, similarity |

### Sequencing

Cases depend on work already planned, which is convenient:

1. **Normalization** must land first — `identifiers[]` and `event_time` are the correlation
   substrate
2. **Staleness fields** must exist — case artifacts are derived like any other
3. **Connection-scoped ACL** must exist — case ACL composes with it rather than replacing it

Given those, the case layer itself is additive: a table, a membership table, correlation rules, and
a retrieval filter. The expensive parts are the derived artifacts and the access-control
composition, not the primitive.

### Open questions

- **Case types: fixed or user-defined?** User-defined is more useful and needs the same
  schema-versioning machinery as normalization. Probably reuse it rather than build a second.
- **Should inferred membership be surfaced to end users at all**, or held for review by an operator?
  Domain-dependent; clinical and legal argue for review-first.
- **Cross-project cases.** A patient seen by two departments in different projects — does the case
  span them, or does each project hold its own? Spanning breaks the project isolation boundary that
  everything else relies on.


---

## Normalization

### Three layers, currently conflated

The proxy already supports `?normalize=contact|calendar_event` — the right idea, covering two
types, on the *outbound* path rather than on ingestion.

| Layer | Transforms | Status |
|-------|-----------|--------|
| **L1 Transport** | provider payload → `UniversalEnvelope` | built — gateway |
| **L2 Domain** | provider record → canonical object | **the gap** — 2 types, proxy-only |
| **L3 Output** | agent result → schema | built — `schema_override` |

### Where it belongs

Normalization is its own stage, *before* enrichment — not inside the agents.

```
classify → NORMALIZE → route → enrich (LLM) → embed → entities → graph
              ▲
              │ provider profile mapping + target schema
              │ (mem-dog standard OR user standard)
```

Three reasons it cannot live inside the agents:

1. **~60% of the catalog is JSON records** that should normalize with no inference call at all.
2. **It makes entity extraction structural rather than inferred.** If a Salesforce record
   normalizes to a canonical `Person`, you do not need an LLM to *guess* there is a person here.
3. **It must be regenerable independently.** Schemas change; re-normalizing should not require
   re-running expensive enrichment.

### Canonical types

Aligned with the existing eight graph entity types rather than a parallel taxonomy — a mismatch
creates permanent translation loss.

| Type | Sources that map to it |
|------|------------------------|
| `Person` | CRM contacts, email senders, chat users |
| `Organization` | Accounts, companies, workspaces |
| `Message` | Chat, email, comment, review |
| `Document` | Files, docs, pages, articles |
| `Event` | Calendar, meeting, recording |
| `Task` | Issue, ticket, story, todo |
| `Transaction` | Invoice, payment, charge, order |
| `Activity` | Log, alert, incident, event |

### Customization: mem-dog standard or user standard

Follow the pattern proven by `agent_configs` — schema in the record store, read per invocation,
no redeploy.

```
NormalizationSchema          scope: global | org | project
  target_type                Person | Invoice | <user-defined>
  json_schema                the shape
  version                    versioned, never mutated in place

FieldMapping                 per (provider, target_type)
  source_path  →  target_field
  transform                  cast, split, concat, date-parse, lookup
  required / default
  on_missing                 skip | null | fail
```

**Precedence:** project → org → mem-dog standard. Most specific wins, consistent with the rest of
the tenancy model.

Authoring path, increasing in power: use a standard type → extend it → define a custom type →
override the provider mapping. Most users stop at step one or two.

### Two required fields

Correlation depends on these, so they are not optional extras — see [cases.md](#cases-correlating-information-around-a-subject).

| Field | Why |
|-------|-----|
| **`identifiers[]`** | MRN, docket number, serial, VIN. Makes subject correlation a **join** rather than an inference |
| **`event_time`** | When the thing happened, distinct from when it was ingested. Without it, any timeline built over backfilled data is wrong in a way that looks right |

Both must be extracted per source in the field mapping. An item with no extractable `event_time`
must record that fact rather than silently inheriting ingestion time.

### Two invariants

**Raw is truth; normalized is a derived view.** Never discard the original — normalization is
lossy, schemas evolve, mappings have bugs. Store the projection alongside, tagged with the schema
version that produced it.

**Normalization failure must never lose data.** A record that does not fit lands raw with
`normalization_status = failed` and a reason, and stays retryable.

### LLM-assisted mapping

Worth building a *suggester* — point it at a sample payload, get a proposed mapping. But the
mapping it produces must be stored declaratively and be reviewable. Runtime LLM mapping would be
nondeterministic and expensive, and identical inputs would normalize differently across runs.

### What it unlocks

- **Cheaper, more accurate entity extraction** — structural rather than inferred
- **Typed search facets** — `amount > 10000`, `assignee = X` become filterable
- **Cross-source identity resolution** — the same canonical `Person` from CRM, email and chat gives
  the graph far stronger merge signals than prose does



# Part III · Getting data in

*One write path, and every producer that uses it*


---

## The Write API

One endpoint. Every producer uses it.

Earlier drafts had four write paths — an envelope endpoint for the gateway, an item endpoint for
clients, a batch endpoint for bulk, and an internal queue publish for crawler-discovered files.
Four paths meant four sets of admission control, four places to derive an ACL, four ways to be
disabled, and — as it turned out — an external crawler that could ingest records but not files,
because one of the four was an internal queue it could not reach.

**Everything writes through `POST /api/v1/write`.**

### Why it collapses cleanly

Two abstractions already in the design do the work:

**[`ContentRef`](#ingestion-workers)** unifies the payload. Content is `Inline`, `Stored` in the object
store, or `Pending` behind a provider reference. A gateway envelope, a client upload, a crawled
record and a file the crawler found are all one of those three.

**The producer** unifies the caller. Every writer is preconfigured and registered — that is what
makes a single endpoint safe rather than a free-for-all.

### Producers

Nothing writes anonymously. A producer is registered before it can write, and the registration
carries the policy that used to be scattered across four code paths.

```
Producer
  producer_id     whk_<ulid> | crw_<ulid> | key_<ulid> | upl_<ulid>
  type            webhook | crawler | client | upload | agent
  owner           user_id, org_id, project_id
  connection_id   optional — the ACL scope root
  defaults        project, memory_type, tags
  policy          enrich · priority · rate_limit · quota · budget
  status          draft | enabled | disabled

  inbound_auth    api_key | signature | url_secret | none
  api_key_id      ← when inbound_auth = api_key
  signing_secret  ← when inbound_auth = signature
```

**A user-created API key can be configured onto an inbound producer**, so one credential mechanism
serves both directions. Not every provider can present one, so the producer declares its method —
prefer `api_key` where supported, since it is revocable, attributable and capability-scoped, where
a URL secret is none of those. See [auth](#auth-credentials).

| Producer type | Preconfigured by | Identified as |
|---------------|-----------------|---------------|
| **Inbound channel or app** | the system, at connect time | `whk_<ulid>` |
| **Crawler** | the user, dry-run gated before enabling | `crw_<ulid>` |
| **Client / SDK / MCP** | key issuance | `key_<ulid>` |
| **Upload session** | presign request | `upl_<ulid>` |
| **External crawler / ETL** | a project-scoped key | `key_<ulid>` |

#### What this unifies

Seven things that were specified separately now have one home:

| Concern | Now |
|---------|-----|
| **ACL inheritance** | Derived from the producer's connection scope — one rule, all paths |
| **Admission control** | One place: quota, budget, queue depth, payload size |
| **Enable / disable** | `producer.status`. A disabled producer accepts and drops, uniformly |
| **Rate limiting** | Per producer, which subsumes per-(provider, user) |
| **Freshness detection** | `producer.seconds_since_last_item` covers webhooks, crawlers *and* clients |
| **Quota attribution** | Per producer, rolling up to project and org |
| **Enrichment policy** | Producer defaults, overridable per write |

The freshness one is worth noting: the highest-value detector in
[telemetry](#telemetry) was defined per *connection*. Per *producer* is strictly
better — it also catches a crawler whose selector broke and a client that stopped calling.

### The endpoint

```http
POST /api/v1/write
Authorization: Bearer <credential>
Idempotency-Key: 9f2c...

{
  "producer_id": "crw_01JQRS...",
  "items": [
    {
      "external_id": "0064xx0000ABCDE",
      "content": { "kind": "inline", "text": "..." },
      "metadata": { "tags": ["source:salesforce"] },
      "event_time": "2019-03-14T09:20:00Z",
      "case": { "external_id": "MRN-A12345", "case_type": "patient" }
    },
    {
      "external_id": "att-77812",
      "content": { "kind": "pending",
                   "provider": "google-drive",
                   "resource_id": "1AbC...",
                   "connection_id": "conn_01JQRS..." }
    },
    {
      "external_id": "img-4410",
      "content": { "kind": "stored",
                   "storage_ref": "gs://.../upl_01JQRS/img-4410",
                   "mime_type": "image/heic",
                   "size": 3841204,
                   "checksum": "sha256:..." }
    }
  ],
  "options": { "enrich": true, "priority": "live" }
}
```

```json
207 Multi-Status
{
  "accepted": 3, "failed": 0,
  "results": [
    { "index": 0, "status": "created", "data_id": "data_01J...", "state": "queued" },
    { "index": 1, "status": "created", "data_id": "data_01J...", "state": "fetch_pending" },
    { "index": 2, "status": "updated", "data_id": "data_01J...", "state": "queued" }
  ]
}
```

`items` is always an array — one item is an array of one. Always `207`. The SDK facade hides that
for the single-item case, but the *protocol* has one shape, one set of semantics and one admission
path, which is the entire point.

#### `Pending` closes the gap

Item 1 above is the fix. A crawler that discovers a Drive file writes a `Pending` content
reference through the public API; the API creates the data item and enqueues the fetch. It does
**not** publish to an internal queue.

Which means an external crawler can do exactly what a managed one does — including files. Without
this, the claim that external producers are first-class was false for anything that is not a plain
record.

### What is not the write API

Two endpoints remain separate, for good reasons:

| Endpoint | Why separate |
|----------|-------------|
| `POST /api/v1/uploads` | Issues a presigned URL. It grants capability rather than writing data; the *completion* is an ordinary write with `Stored` content |
| `POST /webhooks/{whk_id}` | The public, provider-facing surface. It accepts whatever shape a provider sends, normalises it, and calls the write API. It is a **translator in front of** the write API, not a second one |

### Two kinds of duplicate

`external_id` dedupes **within** a source: re-crawling the same Salesforce record updates rather
than duplicates. It does nothing across sources.

The same PDF arrives from Drive and as an email attachment. Two producers, two `external_id`
values, both legitimate — and one document, embedded twice, entity-extracted twice, occupying two
places in every result set.

#### Dedupe the derived work, not the provenance

The tempting fix is to reject the second write. That loses something real: **who sent it, when and
in what context is itself information.** The email attachment tells you a person shared it with a
colleague; the Drive copy tells you it lives in a folder. Collapsing them discards that.

So keep both items and **share the expensive derived layer**:

| Layer | Behaviour on a content-hash match |
|-------|----------------------------------|
| Data item | **Both kept** — provenance differs, and provenance is data |
| Content hash | Recorded on both; the match is what links them |
| Chunks, embeddings | **Computed once**, referenced by both |
| Extraction, entities, claims | **Computed once**, attributed to both sources |
| Retrieval | **Deduplicated at query time** — one result, both provenances shown |

That last row is what the user actually experiences: searching does not return the same document
twice, but opening it shows both places it came from.

The cost saving is not incidental either — the derived layer is where nearly all the expense sits,
so deduplicating it is most of the benefit of deduplicating at all.

### The write is synchronous; the enrichment is not

A common misreading worth stating plainly: **`POST /write` commits.** The item is durable and has
an id before the response returns. What is queued is the enrichment.

That is why ingest latency is a database write rather than a model call, and why the pipeline being
down delays enrichment without losing data.

### Readiness is a staircase, and clients need to see it

Three states, not one:

| State | Reached when | What works |
|-------|-------------|------------|
| `stored` | immediately | `GET /data/{id}` |
| `searchable` | after embedding | vector and hybrid retrieval find it |
| `enriched` | after the agent runs | viewpoint, entities, facets, graph |

The per-item `state` in the write response, and a `state` field on reads, exist so clients do not
each invent their own polling heuristic. "I just uploaded it and search cannot find it" is a
support ticket that is not a bug — but only if the state is visible.

These are the two SLIs that component metrics cannot show: **ingest → searchable** and
**ingest → enriched**.

### Admission control, in one place

Evaluated before anything is written:

- Producer exists, is enabled, and the credential is authorised for it
- Item count against the per-request cap
- Payload size against the configured maximum
- Storage quota for the target project
- **Enrichment queue depth** — `429` with `Retry-After` when the backlog is deep
- Token budget when `enrich: true`

Rejecting a write is cheap. Accepting one you cannot process is not.

### Requirements

`FR-ING-8`, `FR-EXT-1..7` and the `FR-CRAWL` series all resolve against this single path. See
functional-requirements.md.


---

## Ingestion Workers

### The core problem

```
INVARIANT   integration credentials must never reach the enrichment pipeline
REALITY     the pipeline is what discovers content is missing
CONSTRAINT  webhook ack deadlines (~3s) forbid downloading inline
CONSTRAINT  queue messages ~1MB — bytes cannot travel in the envelope
```

These four rule out both naive designs: the gateway cannot download everything before acking,
and the pipeline cannot fetch authenticated resources itself.

### Worker taxonomy

Eight classes. Backfill and polling are **not** separate classes — they collapsed into the
[crawler](#crawlers) once it became clear they are two schedules of the same machinery.

| ID | Class | Trigger | Creds | Bounded by | Retry costs |
|----|-------|---------|:-----:|-----------|-------------|
| **W1** | Enrich | content available | AI only | model capacity | tokens |
| **W2** | Fetch | content by reference | via proxy | per-provider rate limit | bandwidth |
| **W3** | Crawl | schedule or manual trigger | via proxy | per-provider + per-host limits | re-discovery only |
| **W4** | Scheduled | cron | via proxy | — | nothing |
| **W6** | Stream | persistent socket | via proxy | one conn / account | reconnect + gap fill |
| **W7** | Reprocess | config or schema change | AI only | model capacity | tokens |
| **W8** | Mutate | upstream revision | AI only | model capacity | tokens |
| **W9** | Retract | delete / erasure request | none | — | nothing |

#### Why fetch and enrich must be separate pools

```
FETCH worker                        ENRICH worker
────────────                        ─────────────
I/O-bound, waiting on network       model-bound, waiting on inference
cheap per task, high concurrency    expensive per task, low concurrency
throttled by upstream 429s          throttled by model capacity
retry costs bandwidth               retry costs tokens
holds integration credentials       holds none
```

In one pool, a 200 MB download parks a GPU-capable slot on a socket, and a provider limiting you
to 5 req/s throttles all inference.

#### Two that need special attention

**W6 (stream) cannot be retrofitted.** Slack Socket Mode, Discord gateway, Telegram long-polling
and WhatsApp bridges hold *one connection per account* — five replicas each holding the same
connection means ingesting everything five times. It needs sharding with leader election plus gap
backfill after a drop. That is a different deployment shape from "scale the pool".

**W7 (reprocess) is load-bearing.** It has surfaced as a prerequisite three separate times: agent
tuning, normalization schema evolution, and index regeneration. Without it, changing a prompt, a
schema or an index generator applies only to future data.

**W8 (mutate) is more urgent than it looks.** Crawlers re-discover constantly, making them the
largest source of mutations in the system. Without W8 every re-crawl either duplicates records or
leaves stale facts valid forever.

### The content contract

Three recorded defects share one root cause — agents branching on provenance fields that can lie:

- `source_type=DOCUMENT` with `mime_type=text/plain` misrouting to the PDF agent
- `is_downloaded=False` triggering a download that fails with "No downloadable URL"
- Attachment envelopes needing `is_downloaded=True` set by hand

#### The fix

Replace provenance branching with a closed sum type. Enrichment must be **unable to tell** how
content arrived.

```
ContentRef =
  │ Inline (text | bytes)
  │ Stored (storage_ref, mime_type, size, checksum)
  │ Pending(provider, resource_id, hints)     ← only W2 ever sees this
```

Two rules make it hold:

1. **`is_downloaded` becomes derived, never assignable** — computed from whether `content_text`,
   `content_b64` or `storage_ref` is present. A field that must be manually kept in sync with
   another field will drift.
2. **Routing keys off sniffed MIME**, with `source_type` demoted to a hint. MIME must be detected
   server-side — a client-declared type is an injection vector that chooses which agent runs.

Inline payloads, fetch workers and uploads all converge on the same two-case type, so an uploaded
PDF and a Drive-fetched one are indistinguishable downstream.

### Classification cascade

Deterministic layers first; the LLM is the last resort and layers 1–6 resolve roughly 80% of
traffic.

| Order | Layer | Example |
|-------|-------|---------|
| 1 | Channel message detection | WhatsApp → `chat_message` |
| 2 | `source_type` field | `"pdf"` → `document_pdf` |
| 3 | Explicit `data_type` | supplied by caller — short-circuits everything |
| 4 | Payload heuristic | `latitude`/`longitude` → `sensor_gps` |
| 5 | MIME registry | `application/json` → `structured_json` |
| 6 | URL extension | `.csv` → `structured_csv` |
| 7 | LLM classifier (fallback) | small-tier model on ambiguous content |
| — | Catch-all | binary-blob agent |

### Extraction prompts — standard, or overridden

Each data type is handled by a typed agent with a **standard extraction prompt**. Those defaults
carry the product's opinion about what matters in a PDF, an email, a support ticket or a sensor
reading — and they will be wrong for somebody.

A legal team wants contractual obligations and liability clauses pulled from a document. A clinical
team wants findings and medications. A support team wants sentiment and escalation risk. The same
`document_pdf` agent, three different extractions.

So four things are overridable per data type:

| Overridable | Effect |
|-------------|--------|
| **System prompt** | What the agent looks for and how it reports it |
| **Output schema** | Extra fields, or a different shape entirely |
| **Model tier** | Cheaper for high-volume types, larger for nuanced ones |
| **Processing flags** | `classify` · `summarize` · `extract_entities` · `extract_actions` · `embed` · `extract_topics` · `analyze_sentiment` |

#### Precedence, and locks

Same model as normalization schemas and settings — one mechanism, not a third:

```
project override  →  org override  →  mem-dog standard
```

Most specific wins, **except where an admin has locked it**. A regulated deployment that must not
have its clinical extraction prompt edited by individual members locks it at org level, and the
lock is the enforcement rather than a convention.

#### An override is a versioning event

This is the connection that matters, and it costs nothing because the machinery already exists.

A prompt is part of the [`generator_version` fingerprint](#versioning-staleness). Changing it
produces a new fingerprint, which marks every artifact that agent produced **stale** — and the
generator registry stores the prompt text, so what produced any given extraction is always
reconstructible.

Which means the editor owes the same impact preview a model change does:

> Changing this prompt marks **84,000 artifacts** stale for `document_pdf`.
> Estimated rebuild: **~40 minutes**, **$0** local / **~$12** cloud.
> [ Rebuild now ] [ Rebuild in background ] [ Leave stale ]

Without it, a prompt edit silently applies to future data only, and the corpus ends up half
extracted one way and half the other — with nothing recording which is which.

#### Test before save

The crawler dry-run pattern, applied to prompts: **run the override against a sample of the
tenant's own data of that type and show the output** before it can be saved.

A prompt that looks reasonable and returns unparseable output is otherwise discovered at 3am across
a backfill. This is cheap, it is the same shape as a mechanism already being built, and it is the
only feedback loop that makes prompt editing a reasonable thing to expose to users at all.

#### The schema is the contract, not the prompt

An override may change *what* is extracted. It must not change *the shape the system promises
downstream*.

Normalization, index construction, entity extraction and the graph all consume agent output by
shape. So:

- Output is **validated against the declared schema regardless of the prompt**
- A schema override extends or replaces the declaration — it does not remove validation
- A response that does not conform fails as a schema violation and is retried, then dead-lettered
  **with the raw output attached**, exactly as any other agent failure

A prompt cannot widen the contract by asking nicely.

#### Content is untrusted; the prompt is only semi-trusted

Two distinct risks, and they need separating.

**Prompt injection from ingested content.** Everything this system processes is untrusted by
definition — it arrives from mailboxes, channels and crawled pages. Content is placed in a
delimited section, instructions are never taken from it, output is schema-constrained, and no agent
gets tool access that content could redirect.

**The override itself.** An org admin's prompt runs over members' data, including data the admin
cannot read. That is legitimate — it is org policy — but it means prompt overrides need the same
treatment as any other privileged configuration: authored by admins, audited on change, and subject
to length and cost caps so a pathological prompt cannot quietly multiply the corpus-wide bill.

#### Defaults ship with the product — and that makes them versioned too

The system arrives with a working prompt for every data type. Nobody has to configure anything to
get useful extraction; overriding is opting *out* of a default, not filling in a blank.

Which raises something easy to miss: **when we ship an improved default prompt, that is a
`generator_version` change for every tenant who never overrode it.**

A product update therefore marks artifacts stale across tenants who made no decision at all. The
handling:

- New defaults apply to **new** data immediately
- Existing artifacts are marked stale but **never auto-rebuilt** — nobody's bill moves because we
  shipped a release
- Affected tenants see a notice with the impact estimate and choose
- Tenants who have overridden are unaffected, because their fingerprint does not include our
  default

The alternative — silently rebuilding on release — spends other people's money on a change they
did not ask for. The other alternative, doing nothing, leaves a corpus permanently split across
prompt generations with nothing recording the split.

#### API

Everything below is available at the same granularity in the UI. The UI is a client of these
endpoints, never a privileged path.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/agents` | List data types and agents, each with override state and lock state |
| `GET /api/v1/agents/{id}/config` | **Effective** config, plus where each field came from — `default`, `org` or `project` — and whether it is locked |
| `PUT /api/v1/agents/{id}/config?scope=org\|project` | Set an override |
| `DELETE /api/v1/agents/{id}/config?scope=…` | Revert to inherited |
| `POST /api/v1/agents/{id}/config/test` | **Run a candidate override against sample data** and return the output — before it can be saved |
| `POST /api/v1/agents/{id}/config/impact` | Staleness estimate — artifacts affected, rebuild duration and cost |
| `PUT /api/v1/agents/{id}/config/lock` | Admin lock against lower-scope override |

`GET .../config` returning **provenance per field** is the one that carries the UI. "This prompt is
inherited from org, this schema is a project override, this tier is the product default, and the
model tier is locked" is the question someone actually has, and it cannot be reconstructed from
three separate reads.

#### UI

| Surface | What it does |
|---------|-------------|
| **Agent list** | Every data type, with its effective source, override badge and lock state at a glance |
| **Editor** | **Side-by-side default vs override** — you cannot sensibly edit a prompt without seeing what you are changing from |
| **Test panel** | Pick a sample item of that type, run the candidate, see the output and whether it validates |
| **Impact preview** | Shown before save, not after — artifacts affected, rebuild time, cost |
| **Revert** | One action back to inherited, at any scope |
| **Lock** | Admin-only; visibly disables the editor below it rather than failing on save |

The lock behaviour matters: a member who edits a locked prompt and only discovers it at save time
has wasted their work. Show the lock in the editor.

#### Requirements

- **FR-PROMPT-1** Each data type MUST have a standard extraction prompt, overridable per project
  and per org with precedence project → org → standard.
- **FR-PROMPT-2** An administrator MUST be able to lock an override against lower-level change.
- **FR-PROMPT-3** A prompt, schema, tier or flag override MUST form part of the artifact's
  `generator_version`, and the change MUST present a staleness impact estimate before applying.
- **FR-PROMPT-4** The generator registry MUST store the prompt text, so any extraction's provenance
  is reconstructible.
- **FR-PROMPT-5** An override MUST be testable against sample data before it can be saved.
- **FR-PROMPT-6** Agent output MUST be schema-validated irrespective of the prompt; an override
  MUST NOT be able to bypass validation.
- **FR-PROMPT-7** Ingested content MUST be treated as untrusted: delimited, never a source of
  instructions, and never able to redirect tool use.
- **FR-PROMPT-8** Override changes MUST be audited, and MUST be subject to length and cost caps.
- **FR-PROMPT-9** The system MUST ship a working default prompt for every data type; configuration
  MUST NOT be required to obtain useful extraction.
- **FR-PROMPT-10** A change to a shipped default MUST apply to new data, MUST mark existing
  artifacts stale, and MUST NOT trigger an automatic rebuild.
- **FR-PROMPT-11** Reading an agent's configuration MUST return the **effective** value together
  with the scope each field was inherited from and whether it is locked.
- **FR-PROMPT-12** Every configuration operation MUST be available through the API at the same
  granularity as the UI.

### Failure policy diverges by class

Fetch failures are usually *about the connection*; enrich failures are usually *about capacity*.
Different remediation, different queues, different alerting.

#### W2 fetch

| Condition | Handling |
|-----------|----------|
| `401` / `403` | **Terminal.** Mark connection `needs_reauth`, surface to user. Blind retry burns the connection and hides the cause. |
| `404` / `410` | Terminal. Resource deleted upstream; keep the record, mark unavailable. |
| `429` | Backoff **and** narrow that provider's token bucket. |
| `5xx` / timeout | Retry with backoff, then dead-letter. |

#### W1 enrich

| Condition | Handling |
|-----------|----------|
| Primary model unavailable | Fall through the chain. Not a failure. |
| Chain exhausted | Retry with backoff — transient capacity, not bad data. |
| Schema violation | Retry N, then dead-letter *with raw output attached*. |
| Content corrupt | Terminal. Re-running will not help. |

### Fan-out

W2 emits `1..N` jobs. A resolved email thread re-queues each attachment as its **own** fetch job
rather than expanding in one pass — so one corrupt attachment in a 40-attachment thread does not
fail the thread. Requires a depth cap and a per-root job budget.

### Rate limiting

Token buckets keyed `(provider, user_id)`. One user syncing a large folder must not consume the
Slack budget of every other user. Bulk work runs at lower priority so historical import never
starves live ingestion.

### Idempotency

Providers redeliver. Key the fetch on `(provider, resource_id, version|etag|checksum)` so a
redelivery is a cache hit rather than a second download and a second data item.


---

## Source Coverage

Bespoke adapters do not scale to a 900-provider catalog. But sorting providers by *retrieval
shape* rather than by vendor shows the work concentrates in a minority — the majority need no
fetch logic at all.

### Providers by retrieval shape

| Shape | Share | Examples | Work needed |
|-------|-------|----------|-------------|
| **Record-only (JSON)** | ~60% | Stripe, Salesforce, HubSpot, Jira, Linear, Zendesk, Shopify | **none** — straight to enrich |
| **File-bearing** | ~20% | Gmail, Drive, Dropbox, Box, OneDrive, S3, Zoom | materialise + fan-out |
| **Stream / chat** | ~12% | Slack, Discord, Telegram, WhatsApp, Teams | persistent connection |
| **Pull-only** | ~8% | G2, Capterra, Trustpilot, Yelp, App Store | [crawler](#crawlers) config |

### Capability profiles, not adapters

Replace per-provider code with declarative profiles interpreted by generic workers. Precedent
exists: `api/app/nango_provider_meta.py` is a static local mapping that synthesises
`app_category`, `capabilities` and `channel_key` because the credential broker does not track
them. This widens an established pattern.

```
ProviderProfile (data, not code)
  arrival        push | poll | stream | pull-only
  reference      inline | id | url | query
  content        record | file | export-only | mixed
  cursor         field name + type          (poll)
  fanout         none | shallow | deep
  export_formats [...]                      (Docs-like sources)
  limits         rps, burst, concurrency
  dedupe_key     which fields identify a resource version
```

#### Four verbs

| Verb | Purpose |
|------|---------|
| `resolve(ref)` | Expand a pointer into concrete resources — this is where fan-out lives |
| `materialise(res)` | Stream bytes to the object store without buffering |
| `export(res, fmt)` | Server-side render — Google Docs have no raw bytes to download |
| `read(res)` | Fetch a structured API record — the cheapest verb, and what most providers need |

**`export` is the verb most likely to be missed.** An adapter model with only `download` cannot
express Google Docs at all.

### Support tiers

| Tier | Count | Promise to the user | What we build |
|------|-------|---------------------|---------------|
| **Tier 1** | ~10 | Full fidelity — attachments, exports, fan-out, incremental sync, deletions tracked | Bespoke adapter with all four verbs |
| **Tier 2** | ~50 | Records and files ingest reliably; normalized to canonical types; searchable by facet | Capability profile + mapping |
| **Tier 3** | ~240 | Connects via OAuth; records ingest as JSON; searchable semantically and by keyword | Nothing per-provider — the default path |

**Tier 3 carries 80% of the catalog, so the generic path must be genuinely good.** If it is an
afterthought, "300+ integrations" is a marketing claim rather than a capability. The good news: a
JSON record already classifies as `structured_json`, routes to the JSON agent, embeds and
entity-extracts on the existing path.

### Verify before building

The credential broker ships its own sync engine — scheduled incremental pulls with cursor state
and change webhooks. That overlaps substantially with the [crawler](#crawlers). Confirm what the
deployed version supports *before* writing a crawler framework; it may collapse into writing a
consumer.


---

## Connector Catalog

"What do we support" has **four honest answers**, and collapsing them is how coverage claims stop
being defensible.

| Layer | Count | Meaning |
|-------|-------|---------|
| **Live** | 4 | Connected and moving data now — Slack, Gmail, Drive; Zoom configured awaiting a test recording |
| **Adapter deployed** | 34 | Gateway code exists, whether or not anyone connected it |
| **Documented** | 84 | Setup guide written under `docs/apps/` |
| **Catalog** | 900+ | The credential broker has an OAuth template |

```
catalog          ████████████████████████████████████████  900+
documented       ████████                                   84
adapter deployed ███                                        34
live             ▌                                           4
```

**The gap between catalog and live is the roadmap.** Tier-3 generic handling closes the top band
cheaply; tier-1 adapter work moves things into the bottom one.

### Shape drives machinery

`record` needs nothing beyond the default path · `file` needs materialise and fan-out ·
`stream` needs a stateful persistent connection · `crawl` needs schedule and watermark state ·
`export` needs server-side rendering.

### Communication & messaging

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Slack** | stream + file | 1 | **live** | Events API today; Socket Mode is the stateful path |
| **WhatsApp Business** | stream + file | 1 | adapter | Voice notes arrive as `opus`/`amr` |
| **Telegram** | stream + file | 1 | adapter | Long-polling — persistent connection |
| Discord | stream + file | 2 | adapter | Gateway socket, one per bot |
| Microsoft Teams | stream + file | 2 | adapter | Also a meetings source |
| Twilio | record | 2 | adapter | SMS/voice events |
| Front | record | 3 | documented | Shared inbox |

### Email & calendar

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Gmail** | file + fan-out | 1 | **live** | `historyId` → messages → attachments; watch expires ~7 days |
| **Outlook** | file + fan-out | 1 | documented | Same shape as Gmail |
| **Google Calendar** | record | 2 | documented | **Underrated** — maps straight to canonical `Event`, no LLM |
| Mailchimp / Mailgun / SendGrid | record | 3 | documented | Campaign and delivery events |

### Storage, documents & meetings

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| **Google Drive** | file + fan-out | 1 | **live** | Folder changes fan out deeply |
| **Google Docs** | export | 1 | documented | **Export only** — no raw bytes exist |
| **Notion** | export | 1 | adapter | Block tree, not a file |
| Dropbox / Box / OneDrive | file + fan-out | 2 | documented | Same verbs as Drive |
| AWS S3 / Azure Blob / GCS | file + crawl | 2 | documented | Bucket sync via the `tree` crawler, not upload |
| Google Sheets | export | 3 | documented | Row-count caps matter |
| **Zoom** | file + fan-out | 1 | configured | One recording → video, audio, transcript, chat |
| Google Meet | file | 2 | adapter | Arrives via Drive |
| Contentful / WordPress | record | 3 | documented | CMS content |

### Work tracking & development

| Connector | Shape | Tier | Status |
|-----------|-------|:----:|--------|
| **Jira** | record + file | 1 | adapter |
| **GitHub** | record + file | 1 | adapter |
| Linear | record | 2 | adapter |
| Asana | record | 2 | adapter |
| ClickUp / Monday / Trello / Basecamp / Todoist | record | 3 | documented |
| GitLab / Bitbucket | record + file | 3 | documented |
| Vercel / Netlify | record | 3 | documented |

### CRM, support, finance, HR

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| Salesforce / HubSpot | record | 2 | adapter | Map to canonical `Person` + `Organization` |
| Pipedrive / Zoho CRM / Freshsales | record | 3 | documented | Same canonical targets |
| Zendesk / Freshdesk / Help Scout | record + file | 3 | documented | Ticket + thread |
| Stripe | record | 2 | adapter | Canonical `Transaction` |
| PayPal / Square / QuickBooks / Xero / Brex / Plaid | record | 3 | documented | Facet-searchable once normalized |
| Shopify / WooCommerce | record | 3 | documented | Orders, products |
| BambooHR / Gusto / Rippling / Workday | record | 3 | documented | **Sensitive PII** — privacy defaults matter most here |

### Observability, reviews, social, data

| Connector | Shape | Tier | Status | Notes |
|-----------|-------|:----:|--------|-------|
| Datadog / Sentry / PagerDuty / Opsgenie / Grafana | record | 2 | adapter | Payload usually complete — no fetch needed |
| Yelp / G2 / Capterra / Trustpilot / TripAdvisor / Google Business / App Store | **crawl** | 2 | adapter | No webhooks — these are why the [crawler](#crawlers) exists |
| Twitter / Reddit / LinkedIn / Instagram / Facebook / YouTube | crawl + file | 3 | documented | Rate limits are the binding constraint |
| BigQuery / Snowflake | query | 3 | documented | Cursor on a column — a `query` crawler |
| Airtable | record | 3 | documented | |

### Three catalog corrections

**OpenAI, Anthropic and Pinecone are documented under `docs/apps/` but are not data connectors.**
The first two are model providers; the third is a vector store. Listing them as integrations
inflates the connector count and confuses the taxonomy.

**Google Calendar is documented but not deployed**, despite being one of the cheapest,
highest-value sources available — `ics` maps to a canonical `Event` with no inference call at all.

**HR connectors carry the most sensitive payloads** in the catalog — compensation, performance,
personal records. In a team-tenancy model their privacy defaults need deciding *before* they are
enabled.


---

## Formats

Tiers of support, not a count. "60+ MIME types" does not mean 60 types are equally well handled.

### Tier A — full extraction and enrichment

| Group | Formats | Extraction | Tier |
|-------|---------|------------|------|
| Text & markup | `txt md rst html csv tsv json yaml xml log` | native parse, **no LLM** | small |
| PDF — digital | `pdf` with a text layer | text extraction | large |
| PDF — scanned | `pdf` image-only | **OCR — separate pipeline** | multimodal |
| Office | `docx xlsx pptx odt ods` | structured parse | large |
| Email | `eml msg mbox` | MIME parse + thread rebuild | medium |
| Images | `jpg png webp heic tiff gif` | caption + OCR | multimodal |
| Audio | `mp3 m4a wav ogg opus amr flac` | transcription + diarization | omni |
| Video | `mp4 mov webm mkv` | audio → transcript, keyframes → caption | omni |
| Code | `py js ts go rs java swift sql sh tf` | language detect, structure | medium |
| Calendar / contacts | `ics vcf` | structured → canonical `Event` / `Person` | small |
| Archives | `zip tar gz 7z` | expand + recurse, capped | — |

**`ics` and `vcf` are the highest value per unit cost in the whole list** — they map directly onto
canonical types with zero LLM involvement. Worth prioritising well above their apparent
importance.

### Tier B — text extraction, shallow structure

`epub` `mobi` `tex` `rtf` `pages` `numbers` `parquet` `sqlite` `gpx` `kml` `geojson` `svg`

### Tier C — stored, metadata-only

DICOM, CAD, 3D meshes, design files, unrecognised binaries. Versioned, tagged and findable by
filename and context; not content-searchable. Nothing is rejected.

### Eight gotchas that decide whether this works

| Issue | Why it bites |
|-------|--------------|
| **HEIC** | The iPhone default — likely the highest-volume image format — and it needs `libheif`, absent from most base images |
| **Digital vs scanned PDF** | Different products. One is cheap text extraction; the other is OCR through a vision model. Detect before routing or the cost model is off by an order of magnitude |
| **`mbox`** | Takeout exports are multi-GB single files holding tens of thousands of messages. Must stream and fan out; one file can legitimately generate 50k jobs |
| **Voice notes** | `amr` and `opus` are what WhatsApp and Telegram actually send — more important than `flac` |
| **Encrypted files** | Password-protected PDFs and zips must fail with a clear status, never crash a worker or retry-loop |
| **Huge spreadsheets** | A 500k-row `xlsx` would blow a workspace embedding budget on one file. Needs chunk strategy plus max-chunks-per-doc |
| **Text encoding** | Latin-1, Windows-1252 and Shift-JIS appear in real exports. Without detection you embed mojibake |
| **Macro-enabled Office** | `docm` / `xlsm` are a malware vector specifically because team members download each other's uploads |

### Cost

Audio and video are the expensive tier by a wide margin. Ten hours of uploaded video, transcribed
and keyframe-captioned on a user's own API key, is a large unbudgeted bill. Gate media
transcription behind explicit per-org opt-in with a visible cost estimate.


---

## Direct Uploads

### Where the current path breaks

Today uploads put content in the request body of an ordinary write. Three limits:

1. **The load balancer allows 120s** on the API path. Anything slower dies at the edge.
2. **The API is the wrong place for bytes.** It is the sole writer of record running min 2 / max
   10 replicas, and autoscaling triggers on request rate — a six-minute upload counts the same as
   a 20 ms write, so uploads starve the pool without triggering a scale-up.
3. **No resumability.** A dropped connection at 90% means starting over.

### Bytes must not touch the API

```
1. POST /api/v1/uploads          → check quota, issue presigned URL + upload_id
2. PUT  <presigned URL>          → client streams DIRECTLY to object store
3. POST /api/v1/write            → completion is an ordinary write carrying `Stored`
                                   content; the API validates and sniffs MIME
```

The API handles two small JSON calls; the object store handles the bytes. Resumable and multipart
come from the object store rather than being built.

**An upload is just another producer of `Stored(...)`** — the same `ContentRef` a fetch worker
emits, so no new pipeline path is needed. A bucket notification on step 2 gives bulk file-drop
ingestion for free.

### Controls

| Risk | Control |
|------|---------|
| Leaked presigned URL | Scope to one object path encoding `{org}/{project}/{user}/{id}`; short TTL; content-length cap; method-restricted |
| Quota evasion | Enforce **before issuing** the URL — an upload cannot be stopped mid-flight |
| Orphans | URLs issued but never completed strand objects; lifecycle rules reclaim them |
| Archive bombs | Cap expansion ratio, entry count and depth before recursive fan-out turns a 2 GB zip into 50k jobs |
| Malware | Scanning becomes required once *sharing* exists — one member's upload is another's download |
| MIME spoofing | Sniff server-side at completion; treat the declared type as a hint only |

### Two shapes that are not uploads

- **URL import** is a W2 fetch job
- **"Ingest my S3 bucket"** is a [crawler](#crawlers) with the `tree` strategy — those adapters
  already exist

### Open question

**Default ACL for a file dropped into a team project.** Private-by-default is consistent with
everything else, but users dragging a file into a *team* space often expect team visibility. Very
hard to change once habits form.


---

## Crawlers

Every other ingestion path waits to be told: a webhook fires, a user uploads, an SDK calls. But
most data does not announce itself. A Salesforce org has three years of opportunities nobody will
re-save. A documentation site has four hundred pages and no webhook. A review platform has no
push mechanism at all.

### This simplifies the taxonomy rather than extending it

Backfill and polling are **two schedules of the same thing**. A backfill is a crawl with
full-history scope run once; a poll is a crawl with watermark scope run on an interval; a web
crawl is the same machinery with link traversal as its discovery strategy. One configurable
worker, several discovery strategies — the taxonomy shrinks from nine classes to eight.

### The crawler does not fetch, and does not enrich

It **discovers** and **emits**. Everything downstream already exists.

```
crawler config (stored, versioned)
      ↓
  scheduler (cron · interval · manual)
      ↓
  crawl run ──── discover → frontier
      │              ↓
      │        dedupe store (external_id · etag · hash)
      │              ↓
      └── emit ──▶ POST /api/v1/write
                        │   records → Inline
                        │   files   → Pending  → W2 fetch
                        ↓
                  W1 enrich (unchanged)
```

Because crawlers write through the same contracts as every other producer, a crawled Salesforce
record and a webhook-delivered one are indistinguishable downstream — and a crawler can run
outside the cluster entirely.

### Six discovery strategies

| Strategy | How it enumerates | Example | Incremental by |
|----------|-------------------|---------|----------------|
| **enumerate** | Paginate a collection endpoint | Salesforce Opportunities, Jira issues | modified-since field |
| **query** | Run a query on a schedule | Warehouse table, SOQL, saved search | cursor column |
| **traverse** | Follow links from seeds | Website, wiki, docs site | etag / last-modified |
| **tree** | Walk a hierarchy | Drive folder, S3 prefix, SFTP dir | path + mtime |
| **feed** | Read an index or feed | RSS, sitemap, changelog | entry id / pubdate |
| **search** | Repeat a query, collect results | Social search, review platforms | result id + seen set |

The pull-only connectors in the catalog — review platforms, app stores, warehouses — are all
`search` or `query` crawlers. They stop needing bespoke code and become configuration.

### Anatomy of a config

Declarative and stored, following the pattern already used for agent configs and normalization
schemas — database-resident, versioned, read per run, no redeploy.

| Field | Purpose |
|-------|---------|
| `source` | Connection reference, or `public` for unauthenticated web |
| `strategy` | enumerate · query · traverse · tree · feed · search |
| `scope` | Object types, URL patterns, folder roots, filters — **what is in bounds** |
| `schedule` | cron · interval · once · manual |
| `incremental` | Watermark field, cursor, etag mode, or full-refresh |
| `limits` | Max items · max depth · rate · concurrency · **token budget** · wall-clock cap |
| `mapping` | Normalization schema + field mapping to apply |
| `output` | Project, memory type, tags, **ACL — inherited from the connection** |
| `politeness` | Respect robots · crawl-delay · user-agent identity *(traverse only)* |
| `priority` | live · normal · **bulk** — bulk never starves event-driven work |

### Run lifecycle

A three-day backfill must survive a pod restart, so a run is a first-class checkpointed entity.

| Concern | Behaviour |
|---------|-----------|
| Run states | `pending → running → completed \| failed \| cancelled \| partial` |
| Frontier | `queued → in-flight → done \| error \| skipped` |
| Checkpoint | Cursor plus frontier snapshot — resume, never restart |
| Control | Pause, resume, cancel, dry-run |
| Overlap | A run must not start while the previous is live — skip or queue, declared per config |
| Partial success | An error on one item does not fail the run; it is recorded and the run continues |

#### Dry-run is not optional

A misconfigured `traverse` crawler with a loose URL pattern will happily ingest the public
internet on the customer's inference budget. Every config must be runnable in **dry-run** —
enumerate and report counts, fetch nothing, write nothing, spend nothing — before it is scheduled.

### Deduplication and change detection

Without change detection, a nightly crawl of 50,000 records re-embeds 50,000 records every night.
Three layers:

- **`external_id` upsert** — preserves `data_id` so a re-crawl updates rather than duplicates
- **Etag / last-modified** — skip before fetching, the cheapest possible check
- **Content hash** — skip enrichment when bytes are unchanged even if metadata moved

When content *has* changed, that is a revision — which routes into **W8 mutate**: new version,
re-embed, and invalidate the facts derived from the superseded version.

### Web crawling has obligations the others don't

| Requirement | Why |
|-------------|-----|
| **Honour `robots.txt` and crawl-delay** | Non-negotiable; also the cheapest way to avoid being blocked |
| **Identifying user-agent with a contact URL** | Operators need a way to reach you rather than blackhole you |
| Per-host concurrency cap and backoff | One crawler must not degrade someone else's site |
| Scope allowlist, not blocklist | Link traversal escapes any blocklist eventually |
| No authenticated crawling of third-party sites | Crawl what the tenant owns or what is public |
| Provenance tagging | Publicly-sourced content carries different licensing exposure and must be distinguishable at retrieval |

### Customization

Crawling has to reach APIs nobody wrote an adapter for, so the config has to be more expressive
than "pick a strategy". The spectrum runs from declarative to fully external, and **we deliberately
stop before executing user code.**

| Level | What the user supplies | Runs where |
|-------|----------------------|------------|
| **0 · Preset** | A strategy plus scope | Our workers |
| **1 · Templated HTTP** | Request template, pagination shape, extraction paths | Our workers |
| **2 · Expressions** | JMESPath transforms, computed fields, filter predicates | Our workers |
| **3 · External crawler** | Their own code, in their own runtime, writing through our API | **Their infrastructure** |
| **4 · ETL platform** | An existing tool configured to target our ingest API | **Their infrastructure** |

Levels 0–2 are declarative and safe. Levels 3–4 need nothing from us but a good API. **There is no
level between them** — no plugin sandbox, no user-supplied Python in our workers. Running arbitrary
tenant code inside a multi-tenant worker means building a function-as-a-service platform with the
security surface that implies, and the escape hatch is strictly better: their runtime, their
dependencies, their scaling, their blast radius.

#### The templated HTTP strategy

One generic strategy covers most "we need to pull from X" cases without any adapter work:

```yaml
strategy: http
source:  { connection_id: conn_01JQRS... }   # or public

request:
  method: GET
  url: "https://api.example.com/v2/items"
  query:
    updated_since: "{{ watermark }}"
    limit: 200
  headers:
    Accept: application/json

pagination:
  type: cursor            # cursor | offset | page | link_header
  cursor_path: "meta.next_cursor"
  cursor_param: "cursor"
  stop_when: "length(data) == `0`"

extract:
  items_path:   "data[*]"
  id_path:      "id"
  version_path: "updated_at"
  content_path: "body"
  url_path:     "attachments[*].download_url"

transform:
  - target: amount
    expr: "to_number(financials.total_cents) / `100`"
  - target: is_priority
    expr: "priority == 'high' || contains(tags, 'urgent')"

filter:
  include: "status != 'deleted'"
```

Everything above is data. Expressions are JMESPath — a specified query language with no side
effects, no I/O and no loops, so it cannot hang a worker or reach the network. Templates
(`{{ watermark }}`) resolve from a fixed, documented variable set.

That combination — templated request, declared pagination, path extraction, expression transforms —
covers a large majority of REST APIs. When it does not, the answer is level 3, not a bigger DSL.

#### Where the line sits

| Allowed | Not allowed |
|---------|------------|
| JMESPath expressions | Arbitrary code |
| Declared pagination shapes | Custom pagination callbacks |
| Template variables from a fixed set | Template evaluation with side effects |
| Static header and query values, plus connection credentials | Fetching secrets at runtime |
| Regex extraction with a compile timeout | Unbounded backtracking |

Every one of those "not allowed" items is a request someone will make. The answer each time is the
same: run it in your own process and write through the API.

---

### Crawling is a way of adding data — and it does not have to be ours

Crawling belongs alongside webhooks, uploads and the SDK as a **first-class way data enters the
system**. It is not an internal implementation detail of connectors.

Which leads to the more useful framing: **the ingest API is the universal crawler interface.** Our
built-in crawlers are a convenience layer over it. They call the same endpoints an external system
would, hold no special privileges, and take no shortcuts. That was a deliberate choice — see
"the crawler does not fetch, and does not enrich" above — and this is where it pays.

The consequence: **anything can be a crawler.** A customer's Python script. A scheduled GitHub
Action. An n8n or Zapier flow. An Airbyte or Fivetran destination. An internal ETL job that already
has the data and just needs somewhere to put it.

#### What an external crawler needs from us

| Requirement | Status |
|-------------|--------|
| A **project-scoped service key** with `data:write` and nothing more | Designed — see [api.md](#api-contract-surfaces) |
| **`external_id` upsert** so re-runs update rather than duplicate | Specified in the host contract |
| **Idempotency key** on writes so retries are safe | Gap |
| **One write endpoint** taking `items[]` — `POST /api/v1/write` | Specified — see [write-api.md](#the-write-api) |
| Documented **rate limits and quota headers** so a client can self-throttle | Partial |
| Structured errors with a machine-readable `code` | Partial — two error formats today |
| Client helpers in the SDKs | Partial |

The batch endpoint is the real gap. External ETL pushes in bulk — ten thousand rows at a time —
and a per-record POST turns that into ten thousand round trips, ten thousand auth checks and ten
thousand transactions. Bulk producers need a bulk verb, with per-item results so a partial failure
does not fail the batch.

#### Two other external paths that already work

**Per-user webhooks.** An external system that already has push semantics can post to
`whk_<ulid>` directly. Nothing new required.

**The credential broker's own sync engine.** It ships scheduled incremental pulls with cursor state.
If the deployed version supports it, a slice of what we would build as crawlers becomes
configuration in a system we already run — worth confirming before writing the framework, since it
could *remove* work rather than add it.

#### Why this matters strategically

A managed crawler covers the common cases well. It will never cover a customer's bespoke internal
system, their mainframe export, or the API their vendor documented badly in 2011.

Treating the ingest API as the contract means those cases are **supported by default** rather than
requiring us to build an adapter for each. The connector catalog stops being a ceiling on what the
platform can ingest and becomes a floor.

### Agentic crawlers: propose, don't execute

An agent that decides what to crawl next is a runaway loop with a budget attached. The safer
shape is the one already adopted for normalization mappings: **the agent authors the config, a
human approves it, the declarative crawler runs it.**

| | |
|---|---|
| **Good** | Point an agent at an undocumented API; it proposes strategy, scope, pagination and mapping as a reviewable config |
| **Also good** | Agent-assisted extraction *within* a fetched page — that is enrichment, not discovery |
| **Dangerous** | An agent choosing the frontier at runtime. Nondeterministic, unbudgetable, unreproducible, impossible to dry-run |

If runtime-adaptive crawling is wanted later it needs a hard step cap, a hard token budget, a
domain allowlist and a full decision log.

### Support across surfaces

| Surface | Capability |
|---------|------------|
| **API** | CRUD on configs; trigger, pause, resume, cancel runs; list runs; run detail and errors; dry-run |
| **SDK** | Config builders per strategy in the full client; run control in the admin client |
| **UI** | Config editor with live scope preview, **dry-run before save**, run history, progress, per-run errors, spend |
| **Infrastructure** | Own worker pool with its own ceiling — never colocated with enrich |
| **Scheduling** | Per variant: cron container locally, cluster CronJob on GKE, managed scheduler in cloud — behind one interface |

### Worked example: creating a crawler

#### 1 — Create the config

Created **disabled**. You cannot schedule a crawler that has never been dry-run.

```http
POST /api/v1/crawlers
Authorization: Bearer md_...

{
  "name": "Salesforce opportunities",
  "source":      { "connection_id": "conn_01JQRS..." },
  "strategy":    "enumerate",
  "scope":       { "object": "Opportunity",
                   "filter": "StageName != 'Closed Lost'" },
  "incremental": { "mode": "watermark", "field": "SystemModstamp" },
  "schedule":    { "type": "interval", "every": "1h" },
  "limits":      { "max_items": 50000, "rate_per_sec": 5,
                   "token_budget": 200000, "wall_clock": "PT8H" },
  "mapping":     { "schema": "Transaction@2", "field_map": "map_01JQRS..." },
  "output":      { "project_id": "proj_01JQRS...",
                   "memory_type": "factual",
                   "tags": ["source:salesforce"] },
  "priority":    "bulk",
  "overlap":     "skip"
}
```

```json
201 Created
{ "crawler_id": "crw_01JQRS...", "status": "draft",
  "acl": "inherited:personal", "enabled": false }
```

Note `acl` is **reported, not accepted**. Visibility is inherited from the connection's scope —
a crawler cannot widen access to data the connection produces.

Validation happens here, not at first run: connection exists and is owned or shared · the provider
profile supports this strategy · scope fields exist (may require a probe call) · the mapping's
target type matches the schema · limits fit within org quota · for `traverse`, domains are inside
the allowlist. Failures return `422` with per-field detail.

#### 2 — Dry run, which is mandatory

```http
POST /api/v1/crawlers/crw_01JQRS.../dry-run
{ "sample_size": 100 }
```

```json
202 Accepted
{ "run_id": "run_01JQRS...", "mode": "dry" }
```

```http
GET /api/v1/runs/run_01JQRS...
```

```json
{
  "status": "completed",
  "mode": "dry",
  "discovered": 47213,
  "would_fetch": 47213,
  "would_skip_unchanged": 0,
  "estimated": {
    "bytes": "2.1 GB",
    "enrich_jobs": 47213,
    "tokens": 94000000,
    "cost_usd": 0,
    "duration": "PT6H20M"
  },
  "sample": [ { "external_id": "0064...", "title": "Northwind renewal",
                "mapped_preview": { "amount": 48000, "stage": "Negotiation" } } ],
  "warnings": [
    "17 records have no value for watermark field SystemModstamp",
    "estimated token spend is 84% of this org's remaining monthly budget"
  ]
}
```

This is the point at which someone learns the job will create forty-seven thousand items and run
for six hours — **before** it runs, not after.

#### 3 — Enable

```http
PATCH /api/v1/crawlers/crw_01JQRS...
{ "enabled": true }
```

Returns `409` if there is no successful dry-run for the current config version. Editing scope or
strategy invalidates the dry-run and requires another.

---

#### What actually happens on a tick

**Scheduler** — one leader, elected by `pg_try_advisory_lock`:

1. Select due rows from `crawler_schedules`
2. Apply the overlap policy — with `skip`, a still-running previous run means this tick is recorded
   as skipped rather than queued
3. Check org crawl concurrency and quota
4. `INSERT INTO crawl_runs (status='pending')` and publish a `crawl.run` message

**Crawl worker** claims the run:

1. **Pin the config version.** Mid-run edits must not take effect halfway through a six-hour job
2. Load the watermark from the last *successful* run
3. `status → running`, start the heartbeat
4. **Discovery loop** — page through the provider via `/proxy/{provider}`, credentials injected
   there and never held by the worker. For each record:
   - compute `dedupe_key = (provider, external_id, version|etag|content_hash)`
   - look it up in `crawl_seen` — unchanged means increment the skip counter and move on
   - otherwise insert into `crawl_frontier` with `status='queued'`
   - **checkpoint after every page** — cursor plus frontier state, so a crash resumes here
5. **Emit loop**, concurrent with discovery:
   - every item goes through **`POST /api/v1/write`** carrying `external_id`, which upserts
   - records carry `Inline` content; files carry a **`Pending`** reference that the API turns
     into a fetch job — the crawler never publishes to an internal queue, which is what keeps an
     *external* crawler able to do everything a managed one can
   - `updated` rather than `created` means this is a **mutation**, so it routes into W8: new
     version, re-embed, and invalidate facts derived from the superseded version
6. **Advance the watermark only on successful completion**
7. `status → completed | partial | failed`

Downstream is entirely unchanged: the fetch worker materialises bytes to the object store and emits
to `ingest.enrich`; the enrich worker classifies, routes to a typed agent, and writes viewpoint,
embedding and entities. Nothing downstream can tell the item came from a crawler.

#### Step 6 is the one that bites

**Advancing the watermark on a partial run silently loses data forever.** The next run starts after
records it never actually processed, and nothing ever revisits them. Advance only on
`completed` — a `partial` run re-covers its ground on the next tick, which is cheap because dedupe
skips everything that did succeed.

#### Tables

| Table | Holds |
|-------|-------|
| `crawlers` | Config, versioned |
| `crawler_schedules` | Next-due, overlap policy, last tick |
| `crawl_runs` | State, checkpoint, counters, spend, **heartbeat** |
| `crawl_frontier` | Per-item work queue for the active run |
| `crawl_seen` | Dedupe — provider, external_id, version hash, last seen run |
| `crawl_errors` | Per-item failures with reason |

#### Failure behaviour

| Condition | Result |
|-----------|--------|
| Discovery returns `401` | Run **fails**, connection marked `needs_reauth`, **watermark does not advance** |
| Single item fails | Recorded in `crawl_errors`; run continues; ends `partial` |
| Provider returns `429` | Backoff and narrow that provider's bucket — not a failure |
| Worker dies | Heartbeat goes stale; a reaper marks the run `interrupted`; the next tick resumes from checkpoint |
| Budget exhausted mid-run | Run stops as `partial` with a reason; no watermark advance |

#### Control

```http
PATCH /api/v1/runs/run_01JQRS...   { "action": "pause" }
```

`pause` stops claiming new frontier items and lets in-flight work finish, keeping the checkpoint.
`cancel` stops and marks the run cancelled — but keeps the checkpoint, so it stays resumable.
Discarding progress on cancel would make cancelling a six-hour job an irreversible decision.

### Telemetry

Discovery rate · **dedupe hit rate** · frontier depth and size · **run duration against schedule
interval** (a run longer than its interval will overlap forever) · per-host politeness compliance ·
spend per run · **items-discovered trending to zero**, which is the crawler equivalent of a dead
connection and the single most valuable alert.


---

## Bulk Operations

A batch endpoint sounds like an API convenience. It is actually a capacity control, because
**the write is the cheap part**.

### The amplification problem

A batch of 10,000 records does not create 10,000 rows. It creates:

```
10,000 rows written
   → 10,000 enrichment jobs queued
      → 10,000 LLM calls           ← the actual cost
      → 10,000+ embeddings
      → entity extraction, graph writes, index builds
```

A batch endpoint that accepts instantly and then floods the enrichment queue is **worse than no
batch endpoint** — the caller gets a `200`, the platform gets a multi-hour backlog, and every other
tenant's live ingestion queues behind it.

So bulk write needs admission control before it needs efficiency.

### Four decisions

#### 1. Transaction semantics: per-item, not all-or-nothing

One malformed row must not fail 9,999 good ones. Bulk producers expect to retry individual
failures, not resubmit everything.

```http
POST /api/v1/write
Idempotency-Key: 9f2c...
{ "producer_id": "key_01J...", "items": [ {...}, {...} ],
  "options": { "enrich": false } }
```

There is no separate batch endpoint — bulk is the same verb with more items. See
[write-api.md](#the-write-api).

```json
207 Multi-Status
{
  "accepted": 9987,
  "failed": 13,
  "results": [
    { "index": 0, "status": "created", "data_id": "data_01J..." },
    { "index": 1, "status": "updated", "data_id": "data_01J..." },
    { "index": 2, "status": "error",
      "code": "schema_validation_failed",
      "detail": "amount: expected number, got string" }
  ]
}
```

`207` rather than `200` — a client that treats any 2xx as total success will silently drop the
thirteen failures otherwise.

#### 2. Idempotency at both levels

Two different duplicates, two different mechanisms:

| Duplicate | Cause | Mechanism |
|-----------|-------|-----------|
| The **whole batch** replayed | Client timed out, retried | `Idempotency-Key` header, cached response |
| **Individual items** re-sent | Overlapping runs, re-sync | `external_id` upsert |

Both are required. Batch-level alone does not help an ETL job whose windows overlap; item-level
alone means a network retry writes everything twice with fresh identifiers.

#### 3. Bulk defaults to cheap

Borrowing the posture the capacity plan already takes for host document ingest: **bulk writes
should not trigger full enrichment by default.**

| Flag | Default | Effect |
|------|---------|--------|
| `enrich` | `false` | Store and index structurally; skip LLM analysis |
| `embed` | `true` | Embeddings are cheap and retrieval is useless without them |
| `priority` | `bulk` | Never starves live ingestion |

A caller that wants full enrichment on ten thousand items should have to ask for it, see the
estimate, and have it counted against budget — not get it by accident because the default was
convenient.

#### 4. Admission control happens before acceptance

Checked at request time, before anything is written:

- Item count against the per-request cap (propose 1,000)
- Payload size against `QUOTA_MAX_BODY_BYTES`
- Storage quota for the project
- **Enrichment queue depth** — `429` with `Retry-After` when the backlog is already deep
- Token budget if `enrich: true` — refuse rather than overspend

Rejecting a batch is cheap. Accepting one you cannot process is not.

### Large imports are jobs, not requests

Above the per-request cap, the shape changes. A 500,000-row import is not a request.

**A bulk import is a crawl run whose discovery phase is "read the supplied payload."** Same run
entity, same states, same checkpointing, same progress reporting, same pause/resume/cancel, same
per-item error list. The only difference is where the items come from.

```http
POST /api/v1/imports              → 202 { "run_id": "run_01J..." }
GET  /api/v1/runs/run_01J...      → progress, counts, errors, spend
PATCH /api/v1/runs/run_01J...     → pause | resume | cancel
```

That reuse is worth taking. Bulk import and crawling have identical requirements — resumability,
partial success, progress, budget tracking, cancellation — and building them twice would produce
two subtly different failure models.

### Efficiency, once correctness is settled

| Layer | Naive | Wanted |
|-------|-------|--------|
| Database writes | 10,000 individual inserts | Chunked multi-row inserts (~500/statement) |
| Embeddings | 10,000 single-input calls | Batched calls at the provider's input limit |
| Queue publishes | 10,000 messages | Batched publish |
| Graph writes | Per-entity | Existing `entities/batch` endpoint |

The embedding one matters most — embedding APIs accept many inputs per call, and one-at-a-time
wastes both latency and, on metered providers, money.

### Bulk write is one of five

The same job machinery serves the others, which is the main argument for building it properly once:

| Operation | What it is | Notes |
|-----------|-----------|-------|
| **Bulk write** | Import many items | This document |
| **Bulk reprocess** | W7 — rebuild derived artifacts by selector | Already selector-based; needs the job wrapper |
| **Bulk delete** | W9 — erasure cascade | **The hard one** — see below |
| **Bulk update** | Retag, re-project, change ACL across many items | ACL changes must re-check derived artifacts |
| **Bulk export** | Portability (GDPR Art 20), workspace offboarding | Long-running, resumable, produces an archive |

#### Bulk delete deserves specific attention

> Full treatment in [deletion.md](#deletion).


An erasure request touching 50,000 items is not a `DELETE`. It is a cascading job across data
items, embeddings, entities, graph facts in two stores, object-store blobs, **and any compressed
summary that absorbed the content**.

It must be resumable — a cascade interrupted halfway leaves the corpus in a state where the source
is gone but the derived data is not, which is the worst possible outcome for the request that
triggered it. It must report what it touched, for the audit trail. And it must be verifiable
afterwards, because "we deleted it" is a claim someone may have to stand behind.

This is why the delete cascade cannot be bolted on later: it needs the same job infrastructure as
bulk import, and it needs derived artifacts to carry provenance from the day they are created.

### Requirements

See `FR-EXT-5` and the `FR-CRAWL` series in
functional-requirements.md — bulk import reuses the crawl run
contract, so `FR-CRAWL-7` (checkpointed and resumable), `FR-CRAWL-13` (pause/resume/cancel with
checkpoint preserved) and `FR-CRAWL-20` (heartbeat and reclaim) apply unchanged.



# Part IV · Making it useful

*Indexes, models, and keeping derived data honest*


---

## Index Construction

The pipeline's purpose is not summarization — it is **building retrieval structures**. Classic
inverted indexes map terms that *appear* to documents. An LLM lets you index over vocabulary the
document never contains:

> A ticket saying *"it just spins forever after they hit save"* should be findable by
> *performance regression*, *data loss risk* and *escalation candidate* — none of which are in
> the text.

### The index set

| Index | Built by | Query shape unlocked | Status |
|-------|----------|---------------------|--------|
| Chunk vectors | embedder | "things like this" | built |
| Lexical / BM25 | keyword index | exact terms, names, IDs | built |
| Entity + relationship graph | extractor | who connects to what | built |
| **Structural** | parser | precise citations — page, section path | **near-free** |
| **Normalized facets** | normalizer | `amount > 10000`, `status = open` | **deterministic** |
| **Question index** | LLM | match question-to-question, not question-to-prose | **highest leverage** |
| Claim / fact index | LLM | fact-level citation, contradiction detection | gap |
| Concept index | LLM | inverted index over inferred concepts | gap |
| Summary hierarchy | LLM | both "what is this about" and "what's the number" | gap |
| Intent index | LLM | decisions, action items, commitments | extracted, not indexed |
| Document relations | LLM | supersedes / replies-to / cites | gap |

Two are nearly free and under-exploited — **structural** (the parser already knows it) and
**normalized facets** (no LLM at all). Build those before any expensive one.

Of the LLM-derived indexes the **question index** is highest leverage: most RAG failure is a
mismatch between how people ask and how documents state, and one extra call per chunk closes it.

### Not every item deserves every index

Six index types per item means up to 6× the LLM calls. A log line does not need a question index;
a contract does.

The control surface already exists — see [extraction prompts](#ingestion-workers) — the per-agent processing flags (`extract_entities`,
`extract_actions`, `extract_topics`, `embed`) **are** index-selection flags.

### Build cheap eagerly, expensive lazily

| Tier | What | When |
|------|------|------|
| **eager** | chunks, vectors, FTS, structure, facets | always, on ingest |
| **deferred** | questions, claims, concepts, summaries | on demand |
| **adaptive** | expensive indexes for items that actually get retrieved | after N retrievals |

Most corpora have a long cold tail nobody ever queries. The adaptive tier concentrates spend on
content that demonstrably matters, at the cost of a slower first query on cold content.

### Every index is a privacy leak surface

Each derived artifact must inherit the ACL of its **most restrictive source**:

- A **claim** extracted from a private doc is private — but claims are the most tempting thing to
  merge across a corpus
- A **concept index** entry pointing at a restricted item leaks its existence
- The **entity graph** already merges across sources; a fact derived from a private doc surfaced
  to a teammate is a leak with no audit trail
- A **summary hierarchy** spanning mixed-ACL items must take the *intersection*

**The rule:** derived artifacts carry the ACL of their most restrictive source, and retrieval
filters **at query time, never post-rank**. Post-filtering also breaks top-K — ask for 10, filter
to 3.

### Composable retrieval

Five modes and four rerankers are a *preset table*, not an interface. An application wanting
facets plus graph walks plus a time bound has no way to ask. Four axes:

| Axis | Options |
|------|---------|
| `select` | chunks · facts · entities · facets · summaries |
| `match` | vector · lexical · graph walk · question index · concept |
| `filter` | project · memory type · tags · time range · facets · **ACL (always)** |
| `rank` | rrf · mmr · cross-encoder · none |

The existing five modes survive as **named compositions** — `hybrid` becomes *match: vector +
lexical, rank: rrf*. Presets stay for the simple case; the axes exist for the ones that need them.


---

## Model Catalog

*Landscape surveyed August 2026. Model families move fast; the catalog is designed to be updated,
and the point of this document is the shape, not the specific version numbers.*

### What this adds to Model Garden

Model Garden today manages **providers**: add an API key, test connectivity, discover what models
that provider exposes. Discovery returns a flat list of model IDs — strings with no properties.

That is not enough to choose with. `qwen3.6:27b` and `gemma4:e4b` are both strings; one needs a
24GB card and one runs on a phone; one does vision and one does not. Users cannot make an informed
choice from a dropdown of identifiers, and smart routing cannot validate an assignment it knows
nothing about.

The addition is a **curated catalog of model cards** — models with declared properties — that
users browse, select, and assign. Once assigned, smart routing uses them.

### The finding that changes the tier design

The current five-tier model — small, medium, large, **multimodal**, **omni** — was designed
around a real constraint: text models were text-only, so vision and audio needed separate models
in separate tiers.

**That constraint has largely dissolved.** Gemma 4 is natively multimodal at every size (vision,
audio, tools, thinking). Qwen 3.5 spans roughly 0.8B to 122B with every size natively multimodal.
Modality is now a **capability most models have**, not a tier you route to.

Keeping `multimodal` and `omni` as sibling tiers to `small`/`medium`/`large` conflates two
independent axes:

```
            CAPACITY  ────────────────────────▶
            small        medium        large
CAPABILITY
  text        ●            ●             ●
  vision      ●            ●             ●      ← used to be one column
  audio       ●            ●             ●      ← used to be one column
  tools       ●            ●             ●
```

Routing should select on **capacity tier × required capabilities**, and the catalog must declare
capabilities so that selection can be validated. A vision task routed to a text-only model should
be a startup error, not a runtime surprise.

Migration is straightforward: keep `multimodal` and `omni` as deprecated aliases that resolve to
*(capacity tier, capability set)* pairs, so existing configs keep working.

### The model card

Each catalog entry declares:

| Field | Purpose |
|-------|---------|
| `id` | Canonical identifier, e.g. `qwen3.6:27b` |
| `family` / `version` / `variant` | Grouping and upgrade paths |
| `architecture` | `dense` or `moe` — with total and active parameters for MoE |
| **`license`** | Apache-2.0 · Gemma terms · community licenses with use restrictions. **Must be surfaced** — some restrict commercial use |
| `context_window` | 32K … 1M — governs chunking and long-document routing |
| **`capabilities`** | `text` · `vision` · `audio` · `tools` · `thinking` · `structured_output` · `embedding` |
| `hardware.min_vram_gb` | Per quantization: q4 / q8 / fp16 |
| `hardware.quantizations` | What's actually available to pull |
| `serving.providers` | Which configured engines can serve it — local, cloud, gateway |
| `serving.pricing` | Per-token cost, or free for local |
| `quality_signals` | Benchmark references, **with the date and caveat attached** |
| `recommended_for` | Suggested capacity tier and agent types |
| `status` | `recommended` · `available` · `deprecated` · `superseded_by: <id>` |
| `card_url` | Link to the upstream model card |

`status` matters more than it looks. mem-dog's defaults currently reference a model generation
that has been superseded — without a `superseded_by` field there is no mechanism to tell users
that, and defaults silently rot.

### Where the catalog comes from

Three sources, intersected:

```
  curated registry          what we ship and maintain
        ∩
  provider discovery        what your configured engines actually serve
        ∩
  hardware feasibility      what your machine can actually run
        ─────────────────────────────────────────────
        = models offered to this user
```

The curated registry follows the pattern already established by `nango_provider_meta.py`: a static
local mapping supplying metadata the upstream API does not provide. Providers tell you a model
exists; they do not tell you its VRAM requirements, its license restrictions, or whether it has
been superseded.

Models discovered from a provider but absent from the registry still appear — as
`status: available`, unvalidated, with a note that capabilities are undeclared. **Never hide a
model the user has access to**; just be honest about what is unknown.

### Indicative catalog (August 2026)

Illustrative of the shape and of what "current" means. Expect this to be stale within months.

| Model | Capacity | Capabilities | Context | Notes |
|-------|----------|--------------|---------|-------|
| `gemma4:e2b` / `e4b` | small | text, vision, audio, tools | — | Nano variants — edge and low-RAM |
| `gemma4:12b` | medium | text, vision, audio, tools, thinking | — | Practical laptop model |
| `gemma4:26b` / `31b` | large | text, vision, audio, tools, thinking | — | Strong vision/multimodal |
| `qwen3.5:4b` | small | natively multimodal, tools, thinking | — | Multimodal at 4B |
| `qwen3.6:27b` | large | text, tools, thinking | — | Fits 24GB at Q4; strong agentic/coding |
| `qwen3.6:35b-a3b` | large | MoE, 3B active | — | Best all-round at 32GB |
| `qwen3-coder:30b` | large | code | 256K | Long-context coding |
| `llama4-scout` / `maverick` | large | text | up to 1M | Long-context retrieval leader |
| `deepseek-v4` | large | text, reasoning | — | High-end reasoning, serious hardware |
| `glm-5.2` | large | text | — | Strong all-round open-weight |
| `mistral-medium-3.5` | large | text | 256K | 128B dense |
| `phi-4-mini` | small | text | — | Very small footprint |

Embedding models are catalogued separately — see below, because they are not interchangeable in
the way these are.

### Selection → routing

Once a user assigns models, routing **validates** rather than trusting:

| Check | Failure mode prevented |
|-------|----------------------|
| **Capability match** | A vision agent assigned a text-only model — fails at ingest, not at config time |
| **Hardware feasibility** | A 70B model selected on a 16GB machine — pulls, then OOMs under load |
| **Context window** | Long documents silently truncated because the assigned model has a 32K window |
| **License** | A use-restricted model assigned in a commercial deployment |
| **Provider reachability** | A model assigned but not served by any configured engine |

Validation runs at **assignment time** with clear errors, and again at startup. The current failure
mode — discovering the mismatch when a document fails to process at 3am — is what this removes.

### Changing a model is a versioning event

This is the part most likely to be under-designed.

A model assignment is part of the **generator version** of every artifact that tier produces.
Switching the medium tier from one model to another does not just affect future work — it makes
every existing summary, entity extraction and classification from that tier **stale**.

So the selection UI has an obligation:

> Changing this model marks **2.1M artifacts** stale.
> Estimated rebuild: **~4.5 hours**, **~$0** (local) / **~$180** (cloud).
> [ Rebuild now ] [ Rebuild in background ] [ Leave stale ]

Without that, "improve the model" is a change users make casually and whose consequences they
discover months later when half the corpus reflects one model and half another. See
[retrieval/versioning.md](#versioning-staleness).

### Embedding models are a different UI

**Embedding model selection must not be a dropdown.**

Vectors from different embedding models occupy incomparable spaces. Changing the embedding model
is a corpus-wide migration, not a configuration change — build a parallel index, backfill it,
verify, then swap.

The catalog carries embedding models with their own fields — `dimensions`, `max_input_tokens`,
`normalization` — and the UI must present the change as a **guided migration with a cost estimate
and a rollback path**, never as a setting.

Related and non-negotiable: **embedding calls must never use the fallback chain.** If the assigned
embedder is unavailable, defer with `embed_status = pending`. Substituting a different model
silently corrupts the index.

### Telemetry

Per-inference, record the **model id and version that actually served the request** — not the one
that was configured. Without it you cannot tell whether output came from the primary or the third
fallback, cannot attribute quality regressions, and cannot identify affected rows after a bad
assignment.

Also worth tracking: per-model latency and cost, fallback depth reached, capability-mismatch
rejections, and pull/warm status for local models.

### Surfaces

| Surface | Capability |
|---------|------------|
| **API** | `GET /ai/catalog` (with filters), `GET /ai/catalog/{id}`, assignment endpoints, `POST /ai/assignments/preview` returning the staleness estimate |
| **UI** | Browsable catalog with capability and hardware filters, model card detail, "runs on your hardware" indicator, assignment with impact preview |
| **SDK** | Catalog listing and assignment in the full client; policy locks in the admin client |
| **Admin** | Org-level allowlist — pin approved models, block others. Ties to the config precedence model |

### Open questions

- **Catalog freshness.** Ship it static and update with releases, or fetch a signed catalog
  periodically? Static is air-gap-friendly; fetched stays current. Probably static with an
  optional refresh.
- **Benchmark claims.** Publishing quality signals invites disagreement and dates badly. Cite with
  dates and link out, or omit and let users judge?
- **Auto-upgrade.** When a model is superseded, offer a one-click migration path with the
  staleness estimate attached — or stay silent and let users choose?

### Sources

- [Hugging Face — Best Open Source and Open-Weight LLMs to Run Locally (2026)](https://huggingface.co/blog/daya-shankar/open-source-llm-models-to-run-locally)
- [Codersera — Open-Source LLM Landscape 2026](https://codersera.com/blog/open-source-llms-landscape-2026/)
- [PromptQuorum — Ollama 2026: best models by use case](https://www.promptquorum.com/local-llms/top-open-source-models-ollama)
- [ComputingForGeeks — Ollama Models Cheat Sheet 2026](https://computingforgeeks.com/ollama-models-cheat-sheet/)
- [Till Freitag — Open-Source LLMs Compared 2026](https://till-freitag.com/en/blog/open-source-llm-comparison)


---

## Model Routing at Bulk

Model Garden and Smart Routing already ship. The gap is not building them — it is that they were
designed for **event-driven, single-item, interactive** enrichment, and every new worker class
violates one of those assumptions.

### Tiers

| Tier | Used for |
|------|----------|
| Small | JSON, CSV, YAML, XML, IoT, classification |
| Medium | Code, email, chat, financial, summarisation |
| Large | PDFs, Office documents, web pages, reasoning |
| ~~Multimodal~~ | Images, visual PDFs, OCR — **deprecated as a tier** |
| ~~Omni~~ | Audio, video — **deprecated as a tier** |
| Embedding | Vector generation — **see the warning below** |

> **The multimodal and omni tiers are obsolete.** They existed because text models were text-only.
> Current model families are natively multimodal at every size, so modality is a *capability* to
> validate, not a tier to route to. Routing should select on **capacity × required capabilities**.
> See [model-catalog.md](#model-catalog).

### Fallback chains

Each path has an ordered chain evaluated left to right; the first available model serves. A
provider outage degrades quality or cost, not availability.

**Two exceptions where fallback is unsafe:**

1. **Embeddings must never fall back.** Different models produce incomparable vector spaces. Defer
   with `embed_status = pending` instead. See [versioning](#versioning-staleness).
2. **Regulated content must never fall back.** Falling through to a third-party provider is an
   undisclosed transfer. See [compliance](#privacy-compliance).

### Two credential classes

| Class | Enrich worker | Held where |
|-------|:-------------:|------------|
| **Integration credentials** (OAuth) | must **not** have | gateway / fetch worker, via proxy |
| **AI provider credentials** | **must** have today | resolved per-item, cached |

The invariant is "zero *integration* credentials". The proposed fix is an **LLM proxy** mirroring
the integration proxy, after which no worker holds a secret of either class.

### Collisions with the worker design

| Intersection | Problem |
|--------------|---------|
| Per-item routing in a shared pool | Every message resolves user → agent → tier → engine → credentials. Workers cannot be pinned to a model |
| **Head-of-line blocking** | One tenant's rate-limited provider stalls shared workers and starves everyone else. Needs per-(user, engine) concurrency caps |
| Credential cache × scaling | The per-worker cache means going from 2 to 40 workers multiplies credential fetches 20× |
| **Bulk operations spend user money** | A 50k-item backfill through a large tier on a user's own key is a large unbudgeted bill. Needs estimation up front, budget caps, forced tier-downgrade for bulk paths |
| Retry × fallback | Chain exhaustion triggers retry; the retry may land on a different model. Same input, different output — corrosive for reprocess |
| Worker vs model capacity | Scaling enrich workers past pod capacity relocates the queue from the broker to the model tier, where it is less observable |


---

## Versioning & Staleness

Versioning exists in exactly two places today: data items get a new version per mutation with
diff tracking, and the temporal graph stamps facts with `valid_at` / `invalid_at`. Everything
*between* — every chunk, embedding, entity, claim, summary and facet — is produced once and never
reconsidered.

### The rule

A derived artifact is a function of **two** inputs:

```
artifact = f(source_version, generator_version)
```

Change either and the artifact is stale. Without recording both, "is this embedding still valid?"
is unanswerable — which is why config changes today silently apply only to future data.

**Generator version is compound**, and every component independently invalidates output: agent
prompt · model identity *and weights* · output schema · embedding model · normalization schema ·
chunking strategy · **parser** (different parsers extract different text from the same PDF).

### Four version surfaces

| Surface | Changes when | Status |
|---------|--------------|--------|
| **Source content** | upstream doc revised, message edited, ticket updated | partial — data items version; connector revisions don't upsert |
| **Generators** | prompt, model, schema, parser or chunker changes | **untracked** |
| **Schemas** | normalization target evolves | designed — versioned, never mutated in place |
| **Facts** | world changes, or we learn we were wrong | partial — valid-time only |

### Embeddings: the one that is actually broken

Embeddings are not merely stale-able — they are **incomparable across models**. A vector from one
embedding model and one from another live in different spaces with different dimensionality.
Cosine similarity between them is a number, and that number is meaningless.

**The documented fallback chain switches between a local embedder and a cloud one.** For
generation that is graceful degradation; for embeddings it silently corrupts ranking, with no
error raised and no way to identify affected rows after the fact.

```
embed request ──┬── normal ──▶ local embedder   (space A, dim 768)
                └── outage ──▶ cloud embedder   (space B, dim 3072)
                                     ↓
                            ONE vector index — mixed spaces
                                     ↓
                            similarity search returns nonsense
```

Three consequences:

1. Every embedding row must carry `model_id` and `dim`, and search must filter to a single space.
   Without the column, affected rows cannot even be identified retroactively.
2. **Embeddings must not silently fall back.** If the primary embedder is unavailable, defer with
   `embed_status = pending` — never substitute a different space.
3. Changing embedding model is a **corpus-wide migration**: build the new index alongside, then
   swap. Never mix.

### Knowledge graph: valid time exists, transaction time does not

| Question | Needs |
|----------|-------|
| "Who was CEO in 2024?" | valid time — **have it** |
| "What did we believe on March 1?" | transaction time — partial |
| "When did we learn we were wrong?" | both — **gap** |

**Nothing invalidates facts when a source is revised.** If a document is superseded, facts
extracted from the old version keep `invalid_at = null` — they remain true forever. Temporal
queries then return confidently wrong answers, which is worse than returning nothing. Source
revision (W8) must set `invalid_at` on facts derived from the superseded version.

A harder case sits behind it: **entity resolution decisions are themselves versioned claims.** If
the graph merges two people and later learns they are distinct, an un-merge is required — and
merges are lossy. Recording the merge as a retractable, evidence-bearing decision rather than a
destructive edit is the only way this stays recoverable.

### The staleness model

The concrete deliverable — what makes W7 targetable rather than a full-corpus rebuild:

| Field | Purpose |
|-------|---------|
| `source_id`, `source_version` | which content produced it — a **list** where several sources contributed |
| `generator_version` | what was **intended** — FK to the immutable generator registry |
| `served_by_model`, `fallback_depth` | what **actually ran**, because the chain may have substituted |
| `produced_at` | transaction time |
| `status` | `current` · `superseded` · `stale` · `failed` |

An artifact is stale when either version moves. A sweep marks affected rows; W7 rebuilds by
priority. Without this, "we changed the summarization prompt" means either re-running the entire
corpus or living with permanent inconsistency — and at 50M rows the first option is not available.

### The fingerprint is a change detector, not a record

`generator_version = sha256(canonical_json({prompt, model_id, schema, parser_version, ...}))` tells
you *that* two artifacts were produced differently. It cannot tell you *how*.

Knowing an artifact came from `a3f2…` and another from `b7c1…` does not let you debug a bad
summary, reproduce a result, roll back a regression, or answer "what instructions produced this
clinical summary?" — which is a real question in a regulated context.

#### A generator registry

Immutable and append-only, keyed by the fingerprint:

```
generator_versions
  generator_version      PK — the fingerprint
  agent_id
  prompt_text            the actual prompt, not a reference to a mutable one
  model_id, provider     the CONFIGURED model
  output_schema
  parser_version, chunker_version, embedder_id
  processing_flags
  created_at, created_by
```

Every derived artifact holds a foreign key into it. The full configuration that produced any
artifact in the corpus is always reconstructible, and a prompt change is a new row rather than an
edit — a mutable prompt breaks the guarantee the fingerprint exists to provide.

---

### The bug: the fingerprint records intent, not what happened

This one matters more than it looks.

The fingerprint is computed from the **configured** model. But the fallback chain may have served a
**different** one — a busy local GPU falls through to a cloud provider, and the artifact is written
as though nothing happened.

```
generator_version = a3f2…    ← says "gemma, medium tier"
actually served   = a cloud model, two hops down the chain
recorded          = nothing
```

So two artifacts with **identical fingerprints** can have been produced by different models. That
breaks the core assumption of the staleness model: that equal fingerprints imply equivalent
provenance.

The usage record already captures `serving_model` — but on the *inference event*, not on the
*artifact*. The artifact is what survives, and it is what a rebuild decision reads.

#### Fix

Record both on the artifact:

| Field | Meaning |
|-------|---------|
| `generator_version` | What was **intended** — the fingerprint, FK to the registry |
| `served_by_model` | What **actually ran** |
| `fallback_depth` | `0` means the primary served it |
| `under_fallback` | Derived — `served_by_model ≠ configured` |

This makes "artifacts produced under fallback" a **selector for reprocess**, which is a genuinely
useful cleanup: after an outage, rebuild exactly the artifacts that degraded, and nothing else.

---

### Derived artifacts need history, not just current state

Reprocess overwrites. That is the obvious implementation and it loses three things:

- **Rollback.** A prompt change that made output worse cannot be undone
- **Comparison.** Evaluating whether a change helped requires old and new side by side
- **The regulated question.** "What did the system say in March?" has no answer

Keep the **previous** version of each derived artifact by default, with retention configurable per
artifact type. Reprocess writes a new version and demotes the old rather than replacing it.

Storage is the objection, and it is real at fifty million rows — so make it a policy: summaries and
claims keep history, chunk embeddings do not. The expensive ones to regenerate are the cheap ones
to keep.

### Two more leaks

- **Compressed summaries.** A summary derived from N items goes stale when any one is revised or
  deleted — and deleting the original does not remove its content from the prose. Summaries must
  record their `(source_id, version)` list.
- **Query provenance.** A cited RAG answer cannot be reproduced or audited unless the index
  versions, model versions and retrieval parameters used are recorded with it.

### Deletion vs history

Hard deletion breaks version history; soft deletion fails erasure requests. The workable split is
to **hard-delete content and retain a metadata-only tombstone** — id, versions, timestamps,
reason — so lineage stays intact and no user content survives.

---

### Requirements

- **FR-VER-1** Every derived artifact MUST record the complete configuration fingerprint that
  produced it, as a reference to an **immutable** generator registry.
- **FR-VER-2** The generator registry MUST store the full configuration — prompt text, model,
  schema, parser and chunker versions — so any artifact's provenance is reconstructible. Registry
  entries MUST NOT be edited; a change is a new entry.
- **FR-VER-3** Every derived artifact MUST record the model that **actually served** it, not only
  the one configured, together with the fallback depth reached.
- **FR-VER-4** Artifacts produced under fallback MUST be identifiable as a selector for reprocess.
- **FR-VER-5** Derived artifacts MUST retain at least the previous version, with retention
  configurable per artifact type.
- **FR-VER-6** Reprocess MUST write a new version and supersede the old, not overwrite it.


---

## Retrieval Quality — Feedback and Conflict

Two things the design detects but never acts on: whether an answer was any good, and what to do
when sources disagree.

### Feedback

Nothing currently captures whether a result was useful. That is the signal that would evaluate a
[prompt override](#ingestion-workers), justify a reranker, or tell you an index type is not
earning its cost — and it is cheap to collect at retrieval time if designed in early, and
impossible to collect retroactively.

| Signal | Kind | Cost to collect |
|--------|------|-----------------|
| Explicit rating on an answer | strong, sparse | a thumb |
| Citation opened | implicit, dense | one event |
| Result opened, then a refined query | implicit — a **negative** signal | free |
| Answer copied or acted on | strong, rare | one event |
| Query abandoned with no interaction | weak negative | free |

**The refinement signal is the most useful and the most overlooked.** A user who searches, opens
nothing, rephrases and searches again has told you the first result set was wrong — with no rating
and no complaint.

#### Feedback cannot be pooled across tenants

The obvious use is to learn a better ranking. The obvious implementation is to learn it from
everyone's feedback at once.

**That leaks.** A model tuned on one tenant's click behaviour encodes what their corpus contains and
what they look for. Ranking learned across tenants is a side channel, and a subtle one — nobody
sees another tenant's document, but the ranking function carries information about it.

So: feedback is **tenant-scoped by default**. Cross-tenant learning is opt-in, aggregated, and
should probably not exist in v1 at all.

#### What it is safe to use immediately

- **Evaluation, not training.** Feedback against a golden set tells you whether a prompt or
  reranker change helped — without any model consuming it
- **Per-tenant reranking signals** — a document repeatedly chosen for similar queries in *this*
  workspace
- **Index-value measurement** — if the question index never contributes to a chosen citation, it is
  not paying for itself

### Conflict

The [claim index](#index-construction) detects contradictions. Nothing says what to do with one.

Two sources say the approval threshold is $5,000 and $10,000. Someone asks. What comes back?

#### Resolve what is resolvable; surface the rest

| Conflict | Resolution |
|----------|-----------|
| **Temporal** — the same fact changed over time | Already solved: `valid_at` / `invalid_at`. Not a conflict, a history |
| **Supersession** — a document revised | The [mutation path](#ingestion-workers) invalidates facts from the superseded version |
| **Source authority** — a system of record disagrees with a chat message | Rank by **declared source authority**, per producer or connection |
| **Genuine disagreement** — two authoritative sources differ | **Surface both.** Do not pick |

#### Source authority is declared, not inferred

A producer carries an authority level. The HR system is authoritative for employment facts; a Slack
message mentioning someone's title is not. That is a configuration a human makes, not something to
infer from confidence scores.

Without it, "most recent wins" becomes the default — which means a passing remark in chat overrides
the system of record because it arrived later.

#### Never silently pick one

When authority does not separate them, the answer says so:

> The approval threshold is **$10,000** according to the Finance Policy (updated March),
> though the Procurement Handbook [2] states $5,000.

**A confident wrong answer is worse than an uncertain right one.** The system knows there is a
conflict — the claim index found it — and hiding that to produce a cleaner sentence is the failure
mode this whole design has been avoiding everywhere else.

### Requirements

- **FR-QUAL-1** Retrieval MUST capture explicit and implicit feedback, including query refinement
  as a negative signal.
- **FR-QUAL-2** Feedback MUST be tenant-scoped. Cross-tenant learning MUST be opt-in and MUST NOT
  be enabled by default.
- **FR-QUAL-3** Feedback MUST be usable for evaluation without being consumed by a model.
- **FR-QUAL-4** Producers MUST carry a declared source-authority level.
- **FR-QUAL-5** Detected conflicts MUST be resolved by authority where it separates them, and
  **surfaced with both positions** where it does not.
- **FR-QUAL-6** An answer MUST NOT silently present one side of a detected conflict.


---

## Multi-Language

Absent from the design until now, and **Phase 1 relevant** — because two of the decisions it forces
are made when the first row is written, and both are corpus migrations afterwards.

A system ingesting mailboxes and chat across an international organisation gets multilingual on day
one, whether or not it was designed for.

### The two Phase-1 decisions

#### 1. The lexical index needs a language per row

Postgres full-text search takes a **language configuration** — it determines stemming and stop
words. Index German text as `english` and stemming is wrong, stop words are wrong, and BM25 quietly
underperforms in a way no error reveals.

So `language` is a **column, set at ingest**, before the lexical index is built. Detected
per item, overridable, and defaulting to a configured project language rather than to `english`.

Retrofitting means re-indexing the corpus.

#### 2. Embedding model choice determines cross-lingual retrieval

A monolingual embedder places "invoice" and "Rechnung" in unrelated regions. A multilingual one
places them near each other, so a query in one language retrieves documents in another.

That is a product decision disguised as a model choice — and per
[versioning](#versioning-staleness), **changing the embedding model is a corpus-wide migration**,
not a setting. It is made in Phase 1 whether deliberately or by default.

| Approach | Cross-lingual retrieval | Cost |
|----------|------------------------|------|
| **Multilingual embedder** | Works | Usually slightly weaker monolingual quality |
| Per-language embedders | **Fails across languages** — separate vector spaces | Better per-language quality |
| Translate then embed | Works | Extra inference per item, translation loss, and the original is what you must cite |

**Recommend a multilingual embedder** unless a deployment is genuinely single-language. The
per-language option is the [vector-space trap](#versioning-staleness) in a new costume: separate
spaces that cannot be compared, arrived at deliberately this time.

### What else changes

| Area | Consideration |
|------|--------------|
| **Chunking** | CJK has no word spaces; sentence boundaries differ. A splitter tuned for English produces bad chunks elsewhere |
| **Extraction prompts** | Does the agent answer in the source language or a canonical one? **Canonical for structured fields, source language for quoted content** — otherwise facets are unfilterable |
| **Normalization** | Dates (`03/04` is ambiguous), numbers (decimal comma), name order, addresses |
| **Entity resolution** | The same organisation across scripts. Transliteration is a real matching problem |
| **Retrieval** | Query language may differ from corpus language — the reason the embedder choice matters |
| **Citations** | Cite the original, never a translation. The user must be able to check it |

### Detection

Deterministic first, as everywhere else: source metadata (an email declares a charset and often a
language), then a fast statistical detector, then the model only for genuinely ambiguous short
text. Store confidence alongside, and mark `unknown` rather than guessing `english` — a wrong
language label is worse than an absent one, because it silently mis-stems.

### Requirements

- **FR-LANG-1** Every item MUST carry a detected language with confidence, set at ingest, before
  lexical indexing.
- **FR-LANG-2** Language MUST default to a configured project language, never to a hardcoded one.
- **FR-LANG-3** The lexical index MUST use the item's language configuration.
- **FR-LANG-4** Undetectable language MUST be recorded as `unknown`, not guessed.
- **FR-LANG-5** Structured extraction output MUST use canonical values; quoted content MUST retain
  its source language.
- **FR-LANG-6** Citations MUST reference the original text, never a translation.



# Part V · Lifecycle

*Removing things, correctly*


---

## Deletion

The most destructive operation in the system, and the one where "it seemed to work" is least
trustworthy — because what remains after a bad delete is invisible.

### Two different operations wearing one word

| | **Cleanup** | **Erasure** |
|---|---|---|
| Intent | "I don't want this any more" | "This person has a legal right to have it gone" |
| Initiated by | user or admin | subject request, or a compliance process |
| Grace period | **yes** — recoverable window | **no** — immediate |
| Legal hold | respected, deletion deferred | respected, returns **partial completion** |
| Audit weight | normal | **the record is the deliverable** |
| Verification | optional | **required** |

Conflating them produces one of two failures: a GDPR erasure that sits in a grace bin for thirty
days is not an erasure, and a user who fat-fingers "delete project" and cannot undo it has been
badly served. Different intents, different behaviour, one API with a `mode`.

### Scope

| Scope | Endpoint |
|-------|----------|
| One item | `DELETE /api/v1/data/{id}` |
| A selection | `POST /api/v1/deletions` with a selector |
| Everything in a project | `DELETE /api/v1/projects/{id}?purge=true` |
| Everything for a subject | `POST /api/v1/deletions` with `subject` |
| Org offboarding | `DELETE /api/v1/organizations/{id}?purge=true` |

Beyond a single item, **deletion is a job, not a request** — cascading across embeddings, chunks,
entities, graph facts, summaries and blobs takes time and must survive a worker restart. It reuses
the run entity from [bulk operations](#bulk-operations): checkpointed, resumable, pausable, with
per-item errors.

#### The selector

```
selector:
  data_ids     [...]
  producer_id  crw_… | whk_… | key_…      everything a source ever wrote
  project_id
  case_id
  tags
  source
  access_level
  time_range   { field: event_time | ingested_at, from, to }
```

**`time_range` must name its clock.** "Delete everything from 2019" means something entirely
different by ingestion time than by event time once a backfill has happened — the same request
either deletes three years of history or deletes nothing. Requiring the field makes the ambiguity
impossible rather than merely documented.

`producer_id` is the one people reach for after a mistake: a crawler misconfigured and ingested the
wrong site, and the fix is "remove everything that producer wrote."

### Dry-run is mandatory for scoped deletes

Same pattern as [crawler configs](#crawlers), for the same reason:

```json
POST /api/v1/deletions   { "selector": {...}, "dry_run": true }

{ "would_delete": { "items": 12403, "embeddings": 91220,
                    "summaries_affected": 340, "blobs_bytes": "8.2 GB" },
  "withheld": { "legal_hold": 22, "reason": "matter M-2291" },
  "shared_entities_retained": 1841,
  "sample": [ … ] }
```

A destructive operation whose blast radius is only visible afterwards is not a safe operation. For
project- and org-scoped purges, dry-run plus explicit confirmation is **required**, not advisory.

### What the cascade actually touches

| Artifact | Behaviour |
|----------|-----------|
| Data item | Hard-deleted; a **metadata-only tombstone** remains — id, versions, timestamps, reason |
| Chunks, embeddings | Deleted |
| Blobs | Deleted from the object store |
| **Entities** | **Reference-counted.** An entity mentioned by fifty documents is not deleted because one is — only its contribution is removed |
| **Memory membership** | Removed. An item held by another memory survives — see [memories](#memories) |
| **Graph facts** | Facts sourced solely from the item are deleted; facts with other sources have that source removed |
| **Summaries** | **Marked stale and rebuilt**, not deleted — see below |
| Derived indexes | Deleted with their source |
| **Audit records** | **Survive.** They record that the deletion happened; deleting them defeats the purpose |

#### Entities and summaries are where naive deletes go wrong

**Deleting an entity because one of its sources went away destroys knowledge that fifty other
documents still support.** Reference counting is the difference between removing a contribution and
removing a fact.

**Summaries are the harder case.** A summary spanning forty items, one of which is erased, still
contains the erased content in prose. Deleting the summary loses value; leaving it is a compliance
failure. The right answer reuses machinery that already exists: **mark it stale and let reprocess
rebuild it from the surviving members.** That is exactly why derived artifacts must record their
source set as a *list* from the first row — see
[privacy foundations](#privacy-foundations).

Without that list, a summary is unerasable, because nothing records that the paragraph someone
wants removed came from the document they are asking about.

### Legal hold returns partial completion

An erasure touching a case under hold does **neither** silent thing:

```json
{ "status": "partial",
  "deleted": 11890,
  "withheld": [ { "case_id": "cas_…", "items": 513,
                  "hold": "hold_…", "authority": "Matter M-2291" } ],
  "requeued_on_release": true }
```

Silently deleting held data destroys evidence someone is legally obliged to preserve. Silently
ignoring the request is a compliance failure dressed as success. The only defensible behaviour is
to do what is possible and say precisely what was not — and to re-queue automatically when the hold
lifts.

### Verification

After an erasure the job runs a verification pass: no derived artifact references the deleted
source, no blob remains, no embedding row survives, no graph fact retains it as its only source.

"We deleted it" is a claim someone may have to stand behind. Verification turns it into a checkable
one, and the result belongs in the audit record.

### Permissions

| Scope | Required |
|-------|----------|
| Own item | owner |
| Selection within a project | `member` for own data, `admin` for others' |
| Project purge | `admin` |
| Org purge | `owner`, plus typed confirmation |
| Subject erasure | `admin`, or an authenticated compliance process |

Every deletion is audited with actor, scope, mode, counts and withholdings — and audit is the one
thing a delete never touches.

### API

```
DELETE /api/v1/data/{id}                     single; idempotent, returns already_gone
POST   /api/v1/deletions                     job — selector, mode, dry_run
GET    /api/v1/deletions/{job_id}            progress, counts, withheld, errors
POST   /api/v1/deletions/{job_id}/confirm    required for project and org scope
POST   /api/v1/deletions/{job_id}/cancel     cleanup mode only, within the grace window
DELETE /api/v1/projects/{id}?purge=true      convenience over the job API
DELETE /api/v1/organizations/{id}?purge=true offboarding
```

The UI is a client of exactly these — dry-run preview, a confirmation step naming what will go and
what is held, progress while it runs, and the result with what was withheld and why.

### Requirements

- **FR-DEL-1** Deletion MUST distinguish **cleanup** (grace period, cancellable) from **erasure**
  (immediate, verified).
- **FR-DEL-2** Deletion beyond a single item MUST be a checkpointed, resumable job.
- **FR-DEL-3** A selector `time_range` MUST name which clock it applies to.
- **FR-DEL-4** Scoped deletion MUST support dry-run, and project- and org-scoped purges MUST
  require it plus explicit confirmation.
- **FR-DEL-5** The cascade MUST reach chunks, embeddings, blobs, derived indexes, entity
  contributions and graph facts.
- **FR-DEL-6** Entities MUST be reference-counted; an entity supported by other sources MUST NOT be
  removed.
- **FR-DEL-7** Summaries containing deleted content MUST be marked stale and rebuilt, not left
  intact and not silently discarded.
- **FR-DEL-8** Deletion MUST leave a metadata-only tombstone and MUST NOT delete audit records.
- **FR-DEL-9** Erasure touching held data MUST return partial completion naming what was withheld,
  and MUST re-queue on release.
- **FR-DEL-10** Erasure MUST run a verification pass, and the result MUST be recorded in the audit
  trail.



# Part VI · Access and privacy

*Who can see what, and what we can prove*


---

## Tenancy & Privacy

### Two tenancy models are in play

The host-SaaS contract states that end-user RBAC is *enforced by the host*. That is coherent when
mem-dog is a backend behind someone else's product. It is **not** what a team model needs.

| | Host-SaaS model | Team model |
|---|---|---|
| Who enforces RBAC | the host application | **mem-dog** |
| Keys held by | host backend | per user |
| `project` means | host workspace | team space |
| Privacy unit | project boundary | **per item, per member** |

**Resolution: one enforcement path.** mem-dog always enforces; the host model becomes the case
where a service identity is a single broad principal. Two implementations kept in sync is the
failure mode to avoid.

### Hierarchy

```
Organization (org_<ulid>)          — team or company
  ├── Members (user_id + role)     — owner / admin / member / viewer
  └── Project (proj_<ulid>)        — team space or host workspace
        ├── Memory                 — scoped to project
        ├── Data                   — associated with memory
        └── Embedding              — scoped to project
```

Scoping is applied by passing `project_id` on create and `?project_id=` on list endpoints.
Omitting it returns everything the user owns, keeping single-tenant deployments unchanged.

### Privacy holes that only appear once orgs are teams

**Connection ownership.** The proxy takes `?user_id=` and fetches that user's credentials. If
authorization is "authenticated to the org" rather than "owns this connection", an admin can read
a member's mail through the proxy. Connections need `personal` vs `shared` scope enforced **at the
proxy**, not hidden in the UI.

**Derived-fact leakage.** A fact extracted from a private document, surfaced to a teammate through
the graph, is a leak with no audit trail.

**Compression leakage.** A summary spanning mixed-ACL items must take the *intersection*, or it
leaks by construction — and deleting the original does not remove it from the prose.

**Cross-tenant entity merging.** A single-database graph means isolation is property-filtering
only. Two orgs both holding "Acme Corp" must not merge — and LLM entity resolution is actively
trying to merge them.

**Retrieval filtering.** ACLs must be applied *in* the query. Post-filtering after ranking
silently breaks top-K and leaks existence.

### The unifying rule

**ACL inheritance follows the connection, not the container.** A connection carries a scope
(`personal` or `shared`) set at connect time. Personal mail connected inside a team org produces
private items regardless of project defaults.

This is what reconciles personal and team memory, and it closes the proxy hole in the same move.

### Access levels

| Level | Visibility |
|-------|-----------|
| `private` | Only the owner (default) |
| `shared` | Owner + users in `shared_with` |
| `public` | Any authenticated user **in the organization** — internal, not public |
| `restricted` | Only users in `shared_with` |

**The `public` level is renamed `org`**, and `public` becomes genuine external sharing — see
[access-model.md](#access-model-principals-sharing-and-settings), which also covers principals, groups, share links and the
admin dual-role.

### Scale posture

Build on the existing capacity plan rather than replacing it — quotas before replicas,
`project_id` always in the vector filter path, temporal graph default-off for host workspaces, a
connection pooler, per-org metrics, and a soak harness from 100 to 1,000 projects.

| Dimension | Target |
|-----------|--------|
| Active workspaces | ~1,000 with traffic in the last 30 days |
| Ingest | 50–100 docs/min sustained; bursts to 300/min for ≤5 min |
| Corpus | Median workspace ≤50k embedding rows; p95 ≤500k; cluster ≤50M |
| Search | p95 semantic/hybrid **< 800 ms** excluding generation |
| Availability | API 99.5% monthly; memory soft-fail preferred over cascade |

**"The record store is the shared fate."** Colocating vector and lexical indexes with records is
what makes the low infrastructure floor possible — and it is why every workspace competes for the
same instance. Filtered ANN search over tens of millions of rows is the load-bearing risk.


---

## Access Model — Principals, Sharing and Settings

### The rename that has to happen first

Today `public` means *"any authenticated user in the organization."* That is **internal**, not
public. Once genuine external sharing exists, the same word means two things and someone will make
a document world-readable believing they made it team-readable.

| Old | New | Means |
|-----|-----|-------|
| `private` | `private` | Owner only — default |
| `shared` | `shared` | Explicit principal list |
| **`public`** | **`org`** | Everyone in the organization |
| — | **`public`** | **Genuinely external, via a share link** |
| `restricted` | `restricted` | Principal list, owner excluded |

Rename before the second meaning exists. Afterwards it is a migration against a field people have
already reasoned about incorrectly.

### Principals, not user IDs

`shared_with` currently holds user identifiers. That does not survive contact with teams: every
membership change requires rewriting every shared item, and it silently fails to revoke when
someone leaves.

Share with a **principal**:

```
principal = user:<id> | group:<id> | project:<id> | org:<id> | public
```

Resolution happens at query time, so a group membership change takes effect immediately and
everywhere — including revocation, which is the direction that matters.

#### Groups

A named set of members within an organization. Sharing with *engineering* rather than enumerating
eleven people is the difference between an access model people use correctly and one they route
around.

```
Group
  group_id     grp_<ulid>
  org_id
  name
  members      user_id[]          — direct
  managed_by   manual | scim | idp_claim
```

`managed_by` matters later: enterprise expects groups to arrive from the identity provider rather
than being maintained twice.

### Public sharing

Genuinely external sharing is the feature most likely to cause an accidental disclosure, so it
carries controls the others do not.

```
ShareLink
  share_id     shr_<ulid>
  data_id | case_id
  created_by, created_at
  expires_at            default: required, not optional
  password              optional
  revoked_at
  access_count, last_accessed_at
```

| Control | Behaviour |
|---------|-----------|
| **Org policy gate** | Public sharing is **disabled by default at org level**. An admin enables it; some orgs never will |
| **Expiry required** | A link with no expiry is a permanent disclosure nobody revisits |
| **Explicit confirmation** | The UI states plainly that the item becomes readable by anyone with the link |
| **Revocable** | Immediately, and revocation is audited |
| **Inventory** | Owner and admin can both list *everything currently shared publicly* — the view that catches the mistake made six months ago |
| **Audited** | Creation, each access, and revocation |

#### Derived artifacts do not follow automatically

Sharing a document publicly must **not** publish its summary, its extracted claims, its entities or
its graph facts.

This is the derived-artifact ACL rule ([indexes](#index-construction)) meeting sharing: a
derived artifact carries the ACL of its **most restrictive source**, and a share widens the source
only. A summary spanning a public document and two private ones stays private — otherwise sharing
one item leaks two.

The practical consequence: a public share exposes the item and, optionally and explicitly, a
purpose-built public rendering. Never the internal derived layer.

### The admin who is also a user

One human, two modes — and conflating them is how admin tooling becomes a privacy hole.

```
identity        a normal user, in an org, with normal data
    +
platform grant  platform:read | platform:admin
```

#### System view shows metadata, not content

| Admin can see | Admin cannot see |
|---------------|------------------|
| System health, queue depth, error rates | Item content |
| Org and project inventory, counts, storage | Search results across tenants |
| Usage, quota and budget consumption | Summaries, entities, extracted claims |
| Producer health, connection status | Case contents |
| Audit records | — |
| Feature flags, global limits | — |

**A platform admin does not get tenant data by default.** Support tooling that shows customer
content by default is a privacy violation that arrives disguised as a feature request.

Where content access is genuinely required — an escalated support case, a clinical emergency — it
goes through **break-glass**: explicit justification, scoped to a subject, time-boxed,
notified to the data owner, and written to the audit store as its own event type.

#### Mode is explicit and separately audited

The same human acting as a tenant user and acting as a platform admin produces **different audit
records**. The session carries the active mode; switching is an auditable event. "Was this
read done as the user or as the operator?" must have an answer.

#### Platform grants are not an org role

`owner`, `admin`, `member`, `viewer` are org-scoped. `platform:*` is orthogonal — a platform admin
holds no elevated rights inside any org they are not a member of.

This is also what finally retires the global unscoped `API_KEY`: system operations get a real
identity with real scopes, and every action is attributable.

### Settings taxonomy

Four levels, with the precedence and locking model already used for normalization and model
configuration.

| Level | Owns | Set by |
|-------|------|--------|
| **Platform** | Storage backend, encryption keys, global limits, feature flags | operator |
| **Org** | Members, groups, roles, quotas, allowed providers, **sharing policy**, retention, connection defaults | owner/admin |
| **Project** | Defaults, normalization schemas, crawlers, producers | admin/member |
| **User** | Profile, password, MFA, API keys, own engines, own agent configs, default project, notifications | the user |

**Precedence:** user → project → org → platform, most specific wins, **except where an admin has
locked a setting.** A locked org setting cannot be overridden below it — that is how "only our
approved model providers" and "public sharing disabled" are enforced rather than suggested.

#### Account settings, concretely

| Group | Contains |
|-------|----------|
| **Identity** | Email, display name, password change, MFA enrolment, linked identities |
| **Credentials** | API keys — create, list with prefix and `last_used_at`, rotate, revoke. Never re-displayed |
| **Workspace** | Default org and project, project switcher |
| **AI** | Engines, model assignments, agent configs — within org policy |
| **Connections** | Connected sources, **personal vs shared scope**, reauthorise, disconnect |
| **Sharing** | What I have shared, with whom, and **what is public** |
| **Privacy** | Export my data, delete my data, view my access history |
| **Notifications** | Connection failures, quota warnings, share access |

That last privacy group is worth building early even in thin form: a user who can see their own
access history is a user who can catch a problem you cannot.

### API and UI parity

**Everything settable in the UI is settable through the API**, at the same granularity and with the
same validation. The UI is a client of the API, never a privileged path.

Two consequences: an embedding host can build its own settings surface, and the settings surface is
testable without a browser.

Control-plane endpoints:

```
/organizations  /organizations/{id}/members  /groups  /projects
/users/me  /users/me/api-keys  /users/me/identities  /users/me/connections
/shares                    ← inventory, revoke
/settings/{scope}          ← get/set with lock state
/platform/health  /platform/orgs  /platform/usage  /platform/audit
/platform/breakglass       ← justification required
```

### Requirements

- **FR-ACC-1** The access level currently named `public` MUST be renamed `org`, and `public` MUST
  mean externally shared.
- **FR-ACC-2** `shared_with` MUST hold principals — user, group, project, org or public — resolved
  at query time.
- **FR-ACC-3** Groups MUST be a first-class primitive within an organization, and MUST support
  external management for later identity-provider integration.
- **FR-ACC-4** Public sharing MUST be disabled by default at org level and enabled explicitly.
- **FR-ACC-5** Share links MUST require an expiry, be revocable, and record creation, each access
  and revocation.
- **FR-ACC-6** Owners and admins MUST be able to list everything currently shared publicly.
- **FR-ACC-7** Sharing an item MUST NOT change the access level of artifacts derived from it.
- **FR-ACC-8** Platform grants MUST be orthogonal to org roles and MUST NOT confer rights within an
  organization the holder is not a member of.
- **FR-ACC-9** A platform admin MUST NOT have access to tenant content by default; content access
  MUST require break-glass with justification, scope, time limit and audit.
- **FR-ACC-10** Acting as a platform admin MUST be an explicit, separately audited mode.
- **FR-ACC-11** Settings MUST resolve user → project → org → platform, and an administrator MUST be
  able to **lock** a setting against override.
- **FR-ACC-12** Every setting available in the UI MUST be available through the API at equal
  granularity.
- **FR-ACC-13** Users MUST be able to view their own access history.


---

## Auth & Credentials

### The requirement

Login/password **and** API keys, on a pluggable identity layer.

### Decided: Firebase for login, keys at the gateway, married on identity

| Path | Mechanism |
|------|-----------|
| **Login / password** | **Firebase Auth** |
| **API keys** | Validated at the **API gateway**, created by users themselves |
| **Inbound (webhooks)** | The **same user-created keys**, configured onto a producer |

The three converge on one canonical identity — that is the marriage, and it happens in the
`identities` table below rather than at the edge.

#### Consequence 1: Firebase and air-gap are incompatible

Firebase is a hosted service. An air-gapped deployment cannot reach its JWKS endpoint, so token
verification fails and nobody can log in.

This does **not** invalidate the air-gap claim — it means the claim belongs to the **local variant
only**, served by a different verifier behind the same seam:

| Variant | Login verifier |
|---------|----------------|
| Cloud, GKE | Firebase (RS256, Google JWKS) |
| **Local / air-gapped** | **Local password** (Argon2id, our own signing key) |

The `TokenVerifier` seam is what makes this two implementations rather than two products. It is
also why the seam is Phase 1 work even though Firebase does not arrive until later: build it now
and local costs an implementation; skip it and local costs a fork.

**What must be stated publicly:** air-gapped operation is a self-hosted capability, not a property
of the hosted product.

#### Consequence 2: gateway API keys are not per-end-user keys

A managed API gateway validates **platform-level** API keys — created in the cloud project, bounded
in number, and not something an end user mints for themselves. They are the wrong primitive for
"every user creates their own key", and they do not scale to one per tenant.

Two ways to reconcile that with wanting validation at the edge:

| Option | How | Trade |
|--------|-----|-------|
| **A · Key exchange** *(recommended)* | User key → short-lived JWT from a token endpoint → gateway validates it against **our** JWKS, same as it validates Firebase | One verification mechanism at the edge, two ways to obtain a token. Costs one round trip, cacheable for the token's lifetime |
| **B · Pass-through** | Gateway handles routing, TLS and coarse rate limiting; the application validates `md_*` keys | Simpler, no exchange — but the gateway is no longer doing authentication, only transport |

**Option A is the one that actually marries them.** Both Firebase login and a user API key end up
as a JWT the gateway verifies against a JWKS, so the backend has exactly one code path for "who is
this" and the gateway has exactly one for "is this valid".

Under A the gateway still enforces its own platform key for coarse abuse control at the edge. That
is a different tier from user identity and should not be confused with it.

### Identity is where they marry

Whatever validated the credential, everything resolves to one canonical internal user:

```
users                          ← canonical, internal, never changes
  user_id                        (existing UUIDs preserved)

identities                     ← many-to-one
  (provider, external_id) → user_id
  provider: firebase | local | apikey | saml
  UNIQUE(provider, external_id)

credentials                    ← local password auth only
  user_id, password_hash (Argon2id)

api_keys                       ← user-created
  key_id, user_id, org_id, project_id
  key_hash                       SHA-256; high-entropy token, no slow KDF needed
  prefix                         for display; the key itself is never re-shown
  capabilities                   data:read · data:write · config:write · admin:*
  expires_at, last_used_at, revoked_at
```

A Firebase login and an API key belonging to the same person land on the same `user_id`, with the
same ACLs and the same tenancy scope. The difference is only what the credential is *permitted* to
do — which is the capability scope, not the identity.

**Firebase UIDs are 28-character strings and existing IDs are UUIDs.** Making `identities` the
permanent design rather than migration scaffolding is what turns that from a rewrite into inserting
rows.

### Inbound uses the same keys — with a caveat

A user-created key can be configured onto an inbound producer, so one credential mechanism serves
both directions.

The caveat is that **not every provider can present one.** Webhook senders differ:

| Provider capability | Producer auth method | Examples |
|--------------------|---------------------|----------|
| Sends custom headers | **`api_key`** — the user's own key | Generic webhooks, most internal systems, ETL |
| Signs the payload | `signature` — shared secret, HMAC verified | Slack, Stripe, GitHub |
| Neither — just POSTs | `url_secret` — the `whk_<ulid>` path is the credential | Simple integrations, legacy systems |

So the producer record declares it:

```
Producer
  ...
  inbound_auth   api_key | signature | url_secret | none
  api_key_id     ← when inbound_auth = api_key
  signing_secret ← when inbound_auth = signature
```

**Prefer `api_key` wherever the provider supports it** — it is revocable per key, attributable to a
user, capability-scoped, and shows up in `last_used_at`. A URL secret is none of those: revoking it
means re-registering the endpoint with the provider, and it leaks through logs and referrers.

Where signature verification is available it should be used **in addition**, not instead — it
authenticates the *payload*, which a bearer credential does not.

### Password auth requirements

| Concern | Requirement |
|---------|-------------|
| Hashing | **Argon2id** (or bcrypt cost ≥12). Never SHA-family |
| Brute force | Per-account **and** per-IP rate limiting, exponential lockout |
| Reset flow | Single-use, short-TTL, side-effect-free tokens |
| Verification | Email confirmation before first ingest |
| Enumeration | Identical response for unknown vs wrong password |
| Sessions | Short access token + revocable refresh token; **revocation list** — pure stateless JWT cannot log anyone out |
| MFA | TOTP — an expectation at team scale |
| Policy | Length-first, breach-list check where available |

Most of this comes free with hosted auth and must be **built** for local. That asymmetry is the
real cost of the air-gapped path.

### API keys under multi-tenancy

Today `md_*` binds to one user. A team system needs scope:

| Key type | Acts as | Use |
|----------|---------|-----|
| **User key** | that user, their ACLs | personal scripts, MCP, SDK |
| **Project key** | project service identity | CI, connectors, host-SaaS |
| **Org key** | org service identity | admin automation |

Required properties, several of which are gaps:

- **Hashed at rest.** Store a hash, indexed; keep a display prefix separately. "O(1) lookup" reads
  like a lookup on key value — if plaintext, one read exposes every tenant.
- **Membership-coupled.** Leaving the org must immediately revoke org access, or offboarding leaks.
- **Bounded.** Expiry, rotation with overlap, revocation, `last_used_at`.
- **Never in a browser.** Enforced, not documented.
- **Capability-scoped** — see [api.md](#api-contract-surfaces).

### Retire the global API key

`API_KEY` grants unscoped access with no `user_id` and bypasses all access control. Tolerable
single-tenant; in a team system with per-item privacy it is a master key that voids every ACL.
Anything that logs "who did this" as *nobody* is incompatible with the privacy model.

### Where credentials live — and five problems

| Class | Encryption | Stored in |
|-------|-----------|-----------|
| OAuth / integration | AES-256-GCM | credential broker's own database |
| AI provider keys | symmetric | `{user_id}/engines/{id}.json` — **the blob store** |
| `md_*` API keys | unspecified | record store, "O(1) lookup" |
| Global `API_KEY` | none | environment variable |
| Webhook signing secrets | unspecified | `webhooks` table |
| Infra credentials | none | orchestrator secrets |

1. **Encryption fails open.** Documented behaviour: if encryption is unavailable, keys are stored
   as plain text with a warning. At 1,000 orgs that is a breach with a log line. **Must fail
   closed.**
2. **One master key for all tenants.** Compromise is total. Needs envelope encryption — KEK in a
   KMS, per-tenant DEKs.
3. **"Set once, never rotate"** is documented for the broker encryption key. An operational dead
   end that fails rotation requirements.
4. **Secrets share a blast radius with user data** — encrypted provider keys sit in the same blob
   store as ingested content.
5. **`md_*` storage is unspecified.** If plaintext, one read yields every tenant's credentials.

> Where each of these physically lives per deployment variant — and why Secret Manager and KMS are
> not interchangeable — is in
> [operations/deployment-variants.md](#deployment-variants).

### The fix is symmetry

Integration credentials already have the right pattern — a proxy injects them so the caller never
holds them. AI provider credentials do the opposite: workers fetch decrypted keys over the network
and cache them for minutes.

```
integration creds  →  /proxy/{provider}    →  worker never holds  ✓ exists
AI provider creds  →  /llm-proxy/{engine}  →  worker never holds  ✗ proposed
```

Mirror the pattern and no worker holds a secret of either class.

### Sequence

1. Identity abstraction + `identities` table — no behaviour change, unblocks everything
2. Asymmetric signing — removes the forge-anywhere weakness
3. Local password auth — Argon2id, lockout, reset, verification
4. API key hardening — hashing, scoping, membership coupling
5. Hosted provider — now just another implementation
6. Retire the global key → scoped platform credentials
7. MFA, then SAML/OIDC when enterprise demands it


---

## Privacy Foundations

Most of [compliance.md](#privacy-compliance) describes capabilities that can be added to a running
system: DSAR tooling, export, SSO, certification. This document is about the subset that **cannot**,
and therefore belongs in the first slice.

### The retrofit cost is not uniform

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

### What belongs in the first slice

#### 1. Access audit, from the moment access control exists

The instant there is an ACL, there is a question about who got past it. Audit starts with the first
read, not with the compliance push.

- Every read of an access-controlled item produces an audit record: who, what, when, which
  credential, which producer or surface
- **Append-only**, with retention measured in years rather than the three days that tracing
  memories keep
- **Separate store** from logs and traces — different retention, different mutability guarantee,
  different threat model
- Written on the read path, so it cannot be skipped by a code path that forgot

This is the `domain_events` store from [telemetry](#telemetry). It exists in
Phase 1 for this reason, not because events are useful for debugging.

#### 2. Provenance on every derived artifact

The [delete cascade](#privacy-compliance) is a Phase 8 capability, but it is only *possible* if every
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

#### 3. Encryption at rest, failing closed

Currently unspecified for user content — credentials are encrypted, content is not documented as
being so.

- Content and derived artifacts encrypted at rest
- **Fail closed.** The documented behaviour for provider keys — plaintext with a warning if
  encryption is unavailable — must not be repeated for content. A misconfiguration must refuse the
  write
- Envelope encryption from the start: a key-encryption key held externally, per-tenant data keys.
  Retrofitting per-tenant keys onto a single-key corpus is a full re-encrypt
- Key rotation possible by design. "Set once, never rotate" is an operational dead end

#### 4. Per-item ACL and query-time filtering

Already in Phase 1 for tenancy reasons. Restating why it is also a privacy foundation: an item
written without an access level has no defensible default later, and post-rank filtering both
degrades results and leaks existence.

#### 5. Content classification

A flag, set at ingest, marking content as regulated or containing personal data.

It gates three things already designed:

- **Classification-gated inference** — regulated content pins to local models and fails closed
  rather than falling through to a third party
- **Erasure scope** — knowing which items are in scope for a subject request
- **Export and residency** — what may leave which boundary

Start deterministic: source-based (an HR connector is PII by construction), pattern-based for
obvious identifiers, and caller-declared. Model-based detection can come later; **the field must
exist from the first write** or classification means re-scanning the corpus.

#### 6. Log discipline

Zero cost, and irreversible if got wrong — a secret or a document body written to a log is in a
system with different retention and different access control, and it stays there.

**Never logged:** item content or excerpts · prompt and completion bodies · credentials, including
in URLs and error payloads · personal identifiers in free text.

Prompts *contain user content*. "Log the prompt for debugging" is a data-exfiltration path that
looks like observability. Where genuinely needed, it goes behind a time-boxed, audited, per-tenant
flag — not a log level.

---

### What can safely wait

Deferring these is a scheduling decision, not a design failure:

DSAR tooling and subject-indexed inventory · export and portability UX · SSO, SAML, SCIM ·
break-glass and ethical walls · legal hold · per-org retention policy · data residency ·
certification · the delete cascade *implementation* — its **hooks** are above.

---

### The two hazards, restated as build order

Both are documented in [compliance.md](#privacy-compliance). Their placement matters here:

| Hazard | When it must be closed |
|--------|----------------------|
| **Fallback chain transmits regulated content to a third party** | **Phase 1**, with the first inference call. It is a policy check before the chain, and it is cheap — but every call made before it exists is an untracked disclosure |
| **Compression defeats erasure** | Hooks in Phase 1–2 (provenance above); the cascade itself later. The hazard is created the moment the first summary is written without its source list |

---

### Requirements

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


---

## Privacy & Compliance

Engineering analysis, not legal advice — but the architectural consequences are concrete and
several are load-bearing.

### The deployment variant decides the regulatory posture

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

### Two hazards in the current design

#### Hazard 1 — the fallback chain crosses a legal boundary invisibly

The documented chain is *local model → cloud model → third-party API*. When the local model is
unavailable, regulated content is **automatically transmitted to a third party**, with no error,
no prompt and no record distinguishing which items took which path.

Under HIPAA that is a disclosure of PHI to a party that may have no BAA. Under GDPR it is an
undisclosed transfer. Architecturally it is the same defect as the embedding-fallback bug — an
automatic substitution that is safe for availability and unsafe for correctness — except the
consequence is legal.

**Fix:** data classification gates the chain. Items marked regulated pin to local inference and
**fail closed** rather than falling back. Every inference call records which provider served it.

#### Hazard 2 — compression defeats erasure

Memory compression summarises N items into prose. On an erasure request the source item is
deleted — but its content **survives inside the summary**, and inside any claim, concept key or
graph fact extracted from it.

A "right to be forgotten" implementation that deletes the row and leaves the substance in derived
text has not erased anything. This is why the delete cascade cannot be deferred: it becomes
exponentially more expensive once a production corpus has compressed mixed-subject content.

### GDPR obligations against current state

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

### HIPAA — and the agent nobody costed

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

### Tracing memories are not an audit log

Observability is persisted as `tracing` memories with a **3-day TTL**, and it is sampled. An audit
trail must be durable, complete and tamper-evident, and must record *who accessed which record
when* — a different dataset with different retention and a different threat model. Three-day
sampled traces cannot answer a breach-scope question about last quarter.

### "Public" needs defining

The `public` access level currently means "any authenticated user in the organization" — which is
*internal*, not public. Three distinct things share the word:

- **Org-visible** — the current meaning; harmless
- **Externally shared** — a link outside the tenant. Not supported, and the feature most likely to
  cause accidental disclosure once added
- **Publicly-sourced** — [crawled](#crawlers) web content, which carries different
  licensing and copyright exposure and must be tagged at ingest so retrieval can distinguish it

### What to build, in order

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



# Part VII · Interfaces and operations

*The contract, and running the thing*


---

## API Contract & Surfaces

Everything reaches the platform through `/api/v1/` — UI, SDKs, MCP server, gateway,
conversational agent and host applications alike. That uniformity is a strength; the problem is
that **privilege is not expressed in the surface**.

### The finding

`md_*` keys bind to a **user** and inherit *all* that user's rights. If you are an org owner, the
key you paste into an MCP client can delete your organization — and the MCP tool list includes a
delete tool.

Keys must be **capability-scoped**, not only identity-scoped:

```
identity   →  who am I acting as       (exists today)
capability →  what may this key do     (missing)
```

### Two planes

| | Control plane | Data plane |
|---|---|---|
| Volume | low | high |
| Privilege | high | per-item ACL |
| Audit | mandatory | sampled |
| Latency | irrelevant | sub-second for agents |
| Surfaces | UI, CLI, REST | UI, SDK, MCP, chat agent, REST |

### Endpoint groups by plane

| Group | Prefix | Plane | Capability scope |
|-------|--------|-------|------------------|
| **Write** | `/write` | data | `data:write` — **the single write path, every producer** |
| Data items | `/data` | data | `data:read` — reads and mutations of existing items |
| Producers | `/producers` | control | `config:write` — register webhooks, crawlers, keys |
| Retrieval | `/search` · `/ai/query` | data | `data:read` |
| Memories | `/memories` | data | `data:read` · `data:write` |
| Graph | `/graph` | data | `data:read` · `data:write` |
| Uploads | `/uploads` | data | `data:write` |
| **Crawlers** | `/crawlers` | control | `config:write` |
| Webhooks | `/webhooks` | control | `config:write` |
| Integrations | `/integrations` | control | `config:write` |
| AI config | `/ai/users/{uid}/…` | control | `config:write` |
| Organizations | `/organizations` | control | `admin:*` |
| API keys | `/api-keys` | control | `admin:*` |
| Infrastructure | `/pods` | control | `admin:*` |
| MCP | `/mcp/sse` | **data only** | `data:read` · `data:write` |

Capability scope becomes a property of the **key**, checked at the router. An MCP key is
*structurally incapable* of reaching `/organizations` — not because the caller lacks a role, but
because the credential does not carry the scope.

### Conventions

| Concern | Today | Needs to be |
|---------|-------|-------------|
| Identifiers | ULID with type prefix — `data_`, `mem_`, `whk_`, `org_`, `proj_` | keep — time-sortable and self-describing |
| Pagination | `?limit=&offset=` | **cursor-based** — deep offsets scan; at 50M rows this is the wrong primitive |
| Errors | **two formats** — `{detail, status_code}` and `{error:{code,message,details,request_id}}` | **converge** on the structured envelope; keep `detail` as a deprecated mirror with a stated removal version |
| Correlation | `X-Request-Id` echoed | **extend** — propagate into the pipeline, not just the API |
| Idempotency | none on writes | **idempotency key** — providers redeliver; so do retrying clients |
| Quota responses | `429` + `Retry-After` + structured code | keep |
| Spec | OpenAPI + Swagger/ReDoc | keep — drives SDK codegen |

### Endpoints the design adds

| Endpoint | Why |
|----------|-----|
| `POST /uploads`, `POST /uploads/{id}/complete` | Presigned direct-to-storage flow |
| `CRUD /crawlers`, `POST /crawlers/{id}/runs`, `POST /crawlers/{id}/dry-run`, `PATCH /runs/{id}` | Crawler configs and run control |
| **`POST /write`** | The one write endpoint. `items[]` always, `207` always — batch is not a separate verb, just the same verb with more items |
| **`/agents/{id}/config`** — get effective + provenance, set, revert, **test**, **impact**, lock | Extraction prompts, schemas, tiers and flags per data type — defaults shipped, overridable per org and project |
| **`PUT /cases`** · `/cases/{id}/members` · `/timeline` · `/retrieve` · `/similar` | Subject correlation — patient timelines, legal matters, asset histories |
| `POST /retrieve` | Composable retrieval; the five modes become presets over it |
| `POST /reprocess` | W7 — rebuild derived artifacts by selector |
| `GET /artifacts/stale` | What needs rebuilding, and why |
| `CRUD /schemas`, `/mappings` | Normalization customization |
| `PATCH /connections/{id}` | Set `personal` / `shared` scope — the ACL-inheritance root |
| `POST /tokens/ephemeral` | Short-lived project-bound token for embeddable widgets |
| **`DELETE /data/{id}`** · **`POST /deletions`** with a selector · project and org purge | Cleanup and erasure. Beyond one item it is a job — see [deletion](#deletion) |

### Six personas, not four roles

| Persona | Scope | Gap today |
|---------|-------|-----------|
| **Platform operator** | deployment, infra, secrets | **missing** — the unscoped global key does this job, unattributably |
| **Org owner** | billing, delete org, all members | — |
| **Org admin** | members, projects, quotas, policy | — |
| **Member** | own data, connections, AI config | the "simple user" |
| **Viewer** | read-only | — |
| **Service identity** | host app, CI, agent | **missing** — every key is a person today |

#### Surface × persona

| Surface | Operator | Owner / Admin | Member | Service |
|---------|----------|---------------|--------|---------|
| **Web UI** | infra only | full control plane | own settings only | — |
| **CLI** | primary | scripting | rare | CI |
| **SDK** | — | some config | data plane | primary |
| **MCP** | — | **no admin tools** | data plane | agent |
| **Chat agent** | — | — | data plane | — |
| **REST** | platform creds | scoped by role | scoped by role | scoped key |

### Config precedence, with locking

| Level | Owns | Persona |
|-------|------|---------|
| **Platform** | storage backend, encryption keys, deployment | operator |
| **Org** | quotas, allowed providers, sharing policy | admin |
| **User** | own engines, agent configs, connections | member |

Model Garden is per-user today. An org admin will need to mandate "only our approved provider", so
precedence needs a **lock** flag: org sets policy, user customizes within it, admin can pin.

### SDK layering

Three layers, not one flat client, so the capability boundary is visible at the call site:

| Layer | Contents |
|-------|----------|
| **Simple facade** | `add()` / `search()` / `chat()` — the 90% case |
| **Full client** | Typed CRUD, scoping, pagination, crawler config builders |
| **Admin client** | Org, members, quotas, keys, run control — **separate import, separate key** |

### Embeddable UI

The host contract forbids end-user browsers holding durable secrets. That rules out shipping a
widget with an embedded key — but not embeddable UI:

```
host backend ──mints──▶ short-lived scoped token ──▶ browser widget
                        (project-bound, read-only, minutes not days)
```

Without it, every host rebuilds retrieval UI from scratch.

### Stability policy

Hosts pin against this surface. Write down what may change inside `/api/v1` — additive fields, new
optional parameters, new endpoints — versus what forces `/api/v2`: removed fields, changed types,
altered defaults, narrowed enums.

### Handle with care

**Conversational admin is appealing and dangerous.** "Delete the marketing project", sent over a
messaging app, executed by an LLM that resolved identity from a phone number, is a bad failure
mode. Keep the chat agent strictly data-plane; gate destructive actions behind confirmation in an
authenticated surface.


---

## Telemetry

### This system's characteristic failure is silence, not errors

Almost every serious failure mode produces **no error**. A revoked connection stops ingesting and
returns nothing. An embedding fallback writes vectors into the wrong space and ranks them anyway.
An unenriched item is still searchable, just worse. Stale facts answer temporal queries
confidently and incorrectly. A disabled webhook returns `200 OK` and drops the payload —
deliberately.

Telemetry therefore cannot be organised around error rates. It has to be organised around
**detecting absence**.

### The silent-failure catalogue

| Failure | Visible symptom | Detector |
|---------|-----------------|----------|
| **Connection expired / revoked** | none — ingestion just stops | `needs_reauth` count, and **per-connection time-since-last-item against its own baseline** |
| **Embedding in wrong space** | degraded ranking | distinct `model_id` count per index — must be exactly 1 |
| **Crawler discovering nothing** | runs succeed, find zero items | discovery count trending to zero against the config's baseline — a changed selector looks identical to "no new data" |
| Enrichment backlog | items searchable but shallow | queue lag; count of items with no viewpoint older than N minutes |
| Stale derived artifacts | old prompts still in effect | stale-artifact count by generator version |
| Stale facts after revision | confidently wrong temporal answers | facts with `invalid_at = null` whose source has a newer version |
| Disabled webhook | `200 OK`, payload dropped | explicit dropped-event counter, not an error rate |
| Fan-out truncation | partial thread ingested | emitted-vs-expected child job count |
| Quota throttling a tenant | slow, not broken | `429` rate by `org_id` |

#### The single highest-value signal

**Time since last item, per connection, compared against that connection's own baseline.** A
workspace that normally delivers 200 messages a day and has delivered none for six hours is
broken — but nothing errored, no alert fired, and the user will not notice until they search for
something that should be there.

### Trace context breaks at the queue

`X-Request-Id` is accepted and echoed by the API, but propagation into the pipeline is optional.
Distributed tracing therefore stops precisely where debugging is hardest — the asynchronous half.

```
gateway ──▶ API ──▶ queue ──╳── fetch worker    (orphan span)
 trace ✓    trace ✓   context   enrich worker   (orphan span)
                      dropped
```

Carry `traceparent` in the message envelope and restore it in the worker. Otherwise the only spans
you have cover the fast synchronous path that rarely fails.

### Per-worker signals

| Worker | Signals that matter |
|--------|--------------------|
| W1 enrich | queue lag · model latency · **fallback depth reached** · token spend by tenant · schema-violation rate |
| W2 fetch | **per-provider 429 rate** · bytes fetched · fetch latency · `needs_reauth` count · fan-out ratio |
| W3 crawl | discovery rate · **dedupe hit rate** · frontier size · **run duration vs interval** · politeness compliance · spend per run |
| W3 runs | checkpoint progress · items remaining · projected completion · **spend so far** |
| W6 stream | connection state · reconnect count · **gap duration** after a drop |
| W7 reprocess | stale count by generator · rebuild throughput |

**Fallback depth** deserves particular attention. A system silently running entirely on its
third-choice model still works — and costs more, answers differently, and indicates the primary
has been down for some time. It is the difference between "healthy" and "healthy-looking".

### Two structural constraints

**Cardinality.** Labelling every metric by `project_id` at ~1,000 workspaces multiplies series
count past what a single metrics store comfortably holds. Label high-volume series by `org_id`,
sample at `project_id`, and keep per-project detail in logs and traces where cardinality is cheap.

**Traces stored as memories cost twice.** Tracing is persisted as a `tracing` memory type with a
3-day TTL. That makes observability searchable by the same engine as everything else — but it also
means trace volume scales with ingest volume, in the same database that is already the shared fate
of every tenant. Measure early; keep an escape hatch to ship traces externally.

### Four signals, not three

OpenTelemetry's three pillars are traces, metrics and logs. This system needs a fourth, and
conflating it with logs is how the compliance story fails.

| Signal | Shape | Retention | Answers |
|--------|-------|-----------|---------|
| **Metrics** | Aggregated, low cardinality, always on | Long, cheap | "Is it healthy? Is it getting worse?" |
| **Traces** | Per-request spans, sampled | Short | "Why was *this one* slow or wrong?" |
| **Logs** | Structured lines, correlated | Medium | "What exactly happened here?" |
| **Domain events** | Durable, business-meaningful, queryable | **Long, immutable** | "Who did what, when — and can we prove it?" |

Domain events are not verbose logs. They are the audit substrate: an erasure request, a break-glass
access, a legal hold, a key rotation. Different retention, different mutability guarantees,
different threat model. See the note above about tracing memories not being an audit log — this is
the thing they are not.

---

### Metric catalog

Named by domain. Every counter carries `org_id`; see the cardinality rules below for what else may
be attached.

#### Ingestion

| Metric | Type | Notes |
|--------|------|-------|
| `ingest.requests` | counter | by `source_type`, `status` |
| `ingest.bytes` | counter | |
| `ingest.items` | counter | by `outcome: created \| updated` — upsert effectiveness |
| `ingest.rejected` | counter | by `reason: quota \| validation \| auth \| body_size` |
| **`ingest.dropped`** | counter | **Disabled webhooks return `200` and drop.** An error rate is correctly zero here, so this needs its own counter |

#### Queue

| Metric | Type | Notes |
|--------|------|-------|
| `queue.depth` | gauge | by subject |
| **`queue.consumer_lag`** | gauge | **Autoscaling signal** — enrich workers block on inference, not CPU |
| `queue.publish` / `ack` / `nack` | counter | |
| `queue.redeliveries` | counter | Spikes indicate ack-deadline pressure |
| `queue.message_age` | histogram | Oldest unprocessed |
| `queue.dlq_depth` | gauge | Should be zero; anything else needs a human |

#### Fetch

| Metric | Type | Notes |
|--------|------|-------|
| `fetch.requests` | counter | by `provider`, `status` |
| `fetch.duration` / `fetch.bytes` | histogram / counter | by provider |
| **`fetch.rate_limited`** | counter | Per provider — drives bucket narrowing |
| **`fetch.needs_reauth`** | gauge | Per provider. Non-zero means data has silently stopped |
| `fetch.fanout_ratio` | histogram | Children emitted per parent — catches truncation |
| `fetch.depth_reached` | histogram | Against the recursion cap |

#### Enrichment

| Metric | Type | Notes |
|--------|------|-------|
| **`enrich.classification_layer`** | counter | **By layer 1–7.** This is how you *measure* the claim that ~80% never reach an LLM, rather than asserting it |
| `enrich.duration` | histogram | by `agent`, `tier` |
| `enrich.agent_invocations` | counter | by agent type |
| `enrich.schema_violations` | counter | by agent — rising means a model or prompt changed under you |
| **`enrich.unenriched_age`** | gauge | Items older than N minutes with no viewpoint |

#### Inference

| Metric | Type | Notes |
|--------|------|-------|
| `inference.calls` | counter | by `serving_model`, `provider`, `purpose`, `status` |
| `inference.duration` | histogram | |
| `inference.tokens` | counter | by `direction: in \| out \| cached` |
| **`inference.fallback_depth`** | histogram | Running entirely on third choice still "works" |
| **`inference.free_to_paid`** | counter | A category change, not a degradation |
| `inference.capability_rejections` | counter | Assignment validation catching mismatches |
| `inference.capacity_wait` | histogram | Time queued for a model, distinct from queue lag |

#### Embedding

| Metric | Type | Notes |
|--------|------|-------|
| **`embed.distinct_models_per_index`** | gauge | **Must be exactly 1.** Any other value means the vector-space corruption happened |
| `embed.batch_size` | histogram | Are we actually batching, or issuing one call per chunk? |
| `embed.pending_backlog` | gauge | Deferred rather than fallen back |

#### Crawl

| Metric | Type | Notes |
|--------|------|-------|
| **`crawl.discovered`** | counter | Per config. **Trending to zero is the crawler equivalent of a dead connection** |
| `crawl.dedupe_hit_rate` | gauge | How much work was avoided |
| `crawl.frontier_size` | gauge | |
| **`crawl.duration_vs_interval`** | ratio | Above 1.0 and runs overlap forever |
| `crawl.robots_denied` | counter | |
| `crawl.runs` | counter | by terminal status |

#### Retrieval

| Metric | Type | Notes |
|--------|------|-------|
| `search.requests` | counter | by `mode`, `reranker` |
| `search.duration` | histogram | Target p95 < 800 ms excluding generation |
| **`search.result_shortfall`** | histogram | Requested minus returned. **Systematic shortfall means ACL filtering is happening after ranking**, which silently degrades every answer |
| `search.zero_results` | counter | |
| `rag.citations_per_answer` | histogram | Zero citations on a non-empty corpus is a quality signal |

#### Storage and database

| Metric | Type | Notes |
|--------|------|-------|
| **`db.pool_saturation`** | gauge | The serverless killer — instances × pool against the ceiling |
| `db.connections_active` | gauge | |
| `db.query_duration` | histogram | by query class |
| `db.statement_timeouts` | counter | |
| `storage.bytes` | gauge | by org; sampled by project |
| `vector.index_size` | gauge | |

#### Derived-artifact health

| Metric | Type | Notes |
|--------|------|-------|
| `artifacts.stale` | gauge | by generator — what W7 has to rebuild |
| `artifacts.rebuild_rate` | counter | |
| **`facts.stale_valid`** | gauge | Facts with `invalid_at = null` whose source has a newer version. Every one is a confidently wrong temporal answer waiting to happen |

#### Cases

| Metric | Type | Notes |
|--------|------|-------|
| `case.members` | histogram | Size distribution |
| `case.inferred_ratio` | gauge | How much membership is guessed rather than asserted |
| **`case.missing_event_time`** | gauge | Every one of these is a timeline entry in the wrong place |
| `case.stale_summaries` | gauge | |

#### Tenancy, quota, security

| Metric | Type | Notes |
|--------|------|-------|
| `quota.rejections` | counter | by org, type |
| `budget.utilization` | gauge | by org and billing account |
| `auth.attempts` | counter | by method, status |
| `auth.lockouts` | counter | |
| `acl.denials` | counter | Should be low; a spike is misconfiguration, not attack |
| **`breakglass.invocations`** | counter | **Always alert.** Never routine |

#### Connection health — the one that matters most

| Metric | Type | Notes |
|--------|------|-------|
| **`connection.seconds_since_last_item`** | gauge | Per connection, compared against **that connection's own baseline** |

Every other detector in this document is narrower than this one.

---

### Trace model

```
ingest.request                          (gateway)
├── gateway.normalize
├── gateway.resolve_identity
├── gateway.tag_integrations            → credential broker
├── api.persist
└── api.publish
        ╎ span link, not parent-child
        ╎
        └── enrich.job                  (worker, minutes later)
            ├── classify                 attr: layer_resolved
            ├── route                    attr: agent, tier
            ├── agent.invoke
            │   └── inference.call       attr: serving_model, provider,
            │                                  fallback_depth, tokens
            ├── embed
            ├── extract_entities
            └── graph.write
```

**The async continuation must be a span link, not a child span.** The parent request ended long
before the worker ran; modelling it as a child produces traces with impossible durations and breaks
every latency percentile that includes them. Link the worker span to the originating trace and
record both.

#### Standard span attributes

`org_id` · `project_id` · `data_id` · `run_id` · `case_id` · `agent` · `tier` ·
`configured_model` · `serving_model` · `provider` · `fallback_depth`

#### Sampling

Head-based sampling for volume, **plus tail-based retention of every error and every trace above a
latency threshold**. Errors are exactly what you sampled away when you needed them.

---

### Log discipline

Structured JSON, always carrying `trace_id`, `span_id`, `org_id`, `project_id`, `request_id`.

**What must never be logged:**

| Never | Why |
|-------|-----|
| Item content, or excerpts | It is user data — logging it copies regulated content into a store with different retention and access control |
| **Prompt and completion bodies** | Prompts *contain* user content. "Log the prompt for debugging" is a data-exfiltration path that looks like observability |
| Credentials, tokens, keys | Including in URLs and error bodies |
| Personal identifiers in free text | Structured fields can be redacted; prose cannot |

Where prompt debugging is genuinely needed, it belongs behind an explicit, time-boxed, audited
per-tenant flag — not a log level.

---

### Domain events

Durable, immutable, queryable. This is the audit substrate, and several entries here exist because
a regulator or a court may ask.

| Event | Why it matters |
|-------|---------------|
| `connection.authorized` / `revoked` / `needs_reauth` | Data provenance and the silent-stop story |
| `crawl.run.*` | started, completed, failed, budget_exhausted |
| `model.assignment.changed` | With the staleness impact that was shown and accepted |
| `schema.version.published` | Normalization changes are generator changes |
| **`erasure.requested` / `completed` / `partially_withheld`** | With what was withheld and under which hold |
| **`hold.applied` / `released`** | Scope and authorising identity |
| **`breakglass.invoked`** | Justification, accessor, case, timestamp |
| `case.created` / `member.asserted` | Membership provenance |
| `key.created` / `rotated` / `revoked` | |
| `quota.exceeded` / `budget.exhausted` | |
| `access.denied` | For ethical-wall and ACL forensics |

---

### Service level indicators

The user-facing ones, which are not the same as the component ones:

| SLI | Target |
|-----|--------|
| API availability | 99.5% monthly |
| Search p95 | < 800 ms, excluding generation |
| **Ingest → searchable** | p95 — when can I find it at all? |
| **Ingest → enriched** | p95 — when is it *good*? |
| Enrichment success rate | Excluding terminal content errors |
| Crawl freshness | Time since last successful run, per config |

The two ingest latencies matter most because they are what a user actually experiences, and neither
is visible from component metrics alone.

---

### Cardinality rules

Getting this wrong takes down the metrics store, which then takes down your ability to see anything.

| Dimension | Metrics | Traces / logs |
|-----------|:-------:|:-------------:|
| `org_id` | yes — bounded at target scale | yes |
| `project_id` | **sample only** | yes |
| `user_id` | **never** — unbounded | yes |
| `model`, `provider`, `agent`, `tier` | yes — bounded | yes |
| `data_id`, `case_id`, `run_id` | **never** | yes |
| `connection_id` | gauge only, for the freshness detector | yes |

---

### Alerts derive from silent failures

Each alert maps to an entry in the silent-failure catalogue above, which is the point — alerting on
CPU and error rate would catch almost none of them.

| Alert | Condition |
|-------|-----------|
| Connection gone quiet | `seconds_since_last_item` beyond that connection's baseline |
| Vector index contaminated | `embed.distinct_models_per_index` ≠ 1 |
| Crawler blind | `crawl.discovered` at zero against its own baseline |
| Enrichment falling behind | `queue.consumer_lag` or `enrich.unenriched_age` rising |
| Running on fallbacks | `inference.fallback_depth` elevated and sustained |
| Paying unexpectedly | `inference.free_to_paid` non-zero |
| Retrieval degraded | `search.result_shortfall` systematic |
| Stale facts accumulating | `facts.stale_valid` rising |
| Pool exhaustion imminent | `db.pool_saturation` above threshold |
| Break-glass used | Any invocation |
| Erasure withheld | Any `partially_withheld` event |

### Cost telemetry becomes enforcement

> Detailed usage-record design, the six ways token counts go wrong, and budget enforcement are in
> [token-accounting.md](#token-accounting).

Token usage is already tracked per user, model and agent. Under multi-tenancy with user-supplied
provider keys that measurement has to become a **control**: budget consumed against cap, spend
projection for a queued backfill *before* it runs, and automatic tier-downgrade or refusal at the
limit.


---

## Token Accounting

`FR-OBS-4` requires tracking LLM token usage per user, per model and per agent. That is the right
instinct and not enough to bill on, budget against, or explain a surprise.

Six things are missing, and each of them biases the number in the same direction: **under-counting**.

### What a usage record has to carry

One record per inference call — not per request, per *call*, since one ingest can trigger several.

```
inference_events
  ts, event_id
  org_id, project_id, user_id
  case_id | run_id | job_id            ← attribution to crawl runs, imports, reprocess
  agent_id, tier, purpose              classify | summarize | embed | rerank | chat | map
  configured_model
  serving_model, serving_provider      ← what actually ran
  fallback_depth
  tokens_in, tokens_out, tokens_cached
  latency_ms
  status                               ok | failed | timeout | refused
  billing_account                      user_key | org_key | platform
  cost_estimate                        nullable; local inference is 0
```

### The six gaps

#### 1. Input and output are not the same token

Providers price them differently — often several times apart. A single `tokens` counter cannot
produce a cost estimate, only a usage figure that looks like one.

#### 2. Failed and retried calls still cost

A call that times out after generating three thousand tokens consumed three thousand tokens. A
retry doubles it. The fallback chain can triple it.

Counting only successful calls under-reports spend **systematically**, and in exactly the direction
that produces surprise bills. Record every call, with status; report success and total separately.

#### 3. Cached tokens are a different rate

Prompt caching changes the arithmetic substantially where a provider offers it, and an agent
re-running the same system prompt across ten thousand items is precisely the workload that benefits.
Track cached input separately or the estimate is wrong in both directions — too high before caching
is enabled, and unimprovable after.

#### 4. Embedding calls consume tokens too

Embeddings are priced per input token and are the *highest-volume* model call in the system — every
chunk of every item. Omitting them from accounting misses the single largest line for bulk
ingestion.

#### 5. Fallback silently changes who pays

This is the one worth staring at.

```
primary:  local model      → $0
fallback: cloud provider   → real money, per token
```

The chain exists so that a provider outage degrades quality rather than availability. But it also
means **a $0 operation can become a paid one with no signal at all**. A local GPU busy for twenty
minutes during a fifty-thousand-item backfill is a bill nobody authorised and nobody was told about.

Cost attribution must follow `serving_provider`, never `configured_model`. And a fallback that
crosses from free to paid deserves its own counter, because it is a category change rather than a
degradation.

The same structural defect appears three times now: fallback that is safe for availability and
unsafe for something else — [correctness](#versioning-staleness) with embeddings,
[legality](#privacy-compliance) with regulated content, and cost here.

#### 6. The new primitives need attribution

Crawl runs, bulk imports, reprocess jobs and cases each need a spend total, and each has a
user-facing reason:

| Primitive | Why it needs a number |
|-----------|----------------------|
| **Crawl run** | The dry-run promised an estimate. Without actuals it is decoration |
| **Bulk import** | Admission control checked a budget; something has to decrement it |
| **Reprocess job** | The staleness preview quoted a rebuild cost |
| **Case** | Per-case cost is a real question in embedded and clinical deployments |

### Estimate versus actual

Every estimate the system shows — dry-run projections, batch admission, staleness rebuild previews —
must be **recorded alongside the eventual actual**.

Two reasons. Estimates that are never checked drift until nobody trusts them, at which point the
dry-run gate becomes a formality people click through. And the ratio is itself a useful signal: a
run that costs three times its estimate usually means the corpus is not what the config assumed.

### Budget enforcement

Three stages, and the middle one is where most systems stop:

| Stage | Mechanism |
|-------|-----------|
| **Estimate** | Before accepting a run, import or reprocess — refuse rather than overspend |
| **Track** | Running total against cap, visible while work is in flight |
| **Enforce** | Soft warn → hard stop → refuse new work |

Mid-operation exhaustion is already specified for crawls: the run stops as `partial` with a reason
and **does not advance its watermark**, so the remaining work is re-covered on the next tick rather
than lost. Bulk imports and reprocess jobs behave the same way.

#### Whose budget

Three economies share one code path, and conflating them produces bad decisions:

| Billing account | Whose money | Enforcement posture |
|-----------------|-------------|--------------------|
| `user_key` | The user's own provider key | Their spend — show it, warn, do not silently cap |
| `org_key` | Org-provided credentials | Org policy applies; admin sets the ceiling |
| `platform` | Ours, in a hosted deployment | Quota with margin; refuse at the limit |

### Read-side cost is unmetered

Write quotas are designed in detail. Retrieval has none — and retrieval is where a single request
can be a thousand times more expensive than another.

| Request | Relative cost |
|---------|--------------|
| Vector search, top-10 | 1× |
| Hybrid with RRF | ~2× |
| `full` mode — all signals, RRF merged | ~5× |
| …plus a cross-encoder reranker | **~100×** — an inference call per candidate |
| …plus RAG generation | **~1000×** — generation dominates everything above it |

#### Rate-limiting by request count is the wrong primitive

A hundred vector searches and a hundred `full`+cross-encoder+chat requests are the same number to a
counter and three orders of magnitude apart in cost. **Quota must be cost-weighted**, priced on the
work a request actually authorises rather than on the fact that it arrived.

Controls, in order of how much they matter:

| Control | Effect |
|---------|--------|
| **Cost-weighted quota** | The only one that survives contact with `full` mode |
| Budget check **before** the expensive stage | Rerank and generation are gated, not the retrieval that precedes them |
| Reranker availability by tier | Cross-encoder is not a default anyone can loop |
| Max candidates into rerank | Bounds the worst case rather than trusting the caller |
| Per-key concurrency | One client cannot occupy the model tier |
| Query timeout | A runaway hybrid query is cancelled, not waited on |

This is both a **cost** vector and a **denial-of-service** vector, and the second is the one that
arrives without malice — a client with a retry loop and an expensive default configuration will do
it by accident.

### Volume

One row per inference call at target ingest rates is millions of rows, so this cannot live in the
same store as user content without becoming a meaningful share of it.

- **Raw events** — short TTL, sized to cover dispute and debugging windows
- **Rollups** — hourly aggregates by (org, model, purpose, status), retained long
- Reporting reads rollups; investigation reads raw

Same shape as the [tracing-memories concern](#telemetry): observability whose volume scales with
ingest volume needs its own retention story, decided deliberately.

### Requirements

Extends `FR-OBS-4`:

- **FR-TOK-1** Every inference call MUST produce a usage record, including calls that fail, time
  out or are retried.
- **FR-TOK-2** Records MUST separate input, output and cached tokens.
- **FR-TOK-3** Embedding calls MUST be accounted alongside generation calls.
- **FR-TOK-4** Cost MUST be attributed to the **serving** provider, never the configured one.
- **FR-TOK-5** A fallback that moves a call from a free provider to a paid one MUST be counted and
  surfaced separately from ordinary fallback.
- **FR-TOK-6** Usage MUST be attributable to a crawl run, bulk import, reprocess job or case where
  one applies.
- **FR-TOK-7** Every estimate the system presents MUST be recorded and reconciled against actuals.
- **FR-TOK-8** Budgets MUST distinguish user-provided, org-provided and platform credentials.
- **FR-TOK-9** Exhausting a budget mid-operation MUST stop the operation as partial, with reason,
  without losing recoverable progress.
- **FR-TOK-10** Raw usage events and long-retention rollups MUST have separate retention.


---

## Testing

### The invariants are the test suite

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

### Shape of the suite

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

### Testing what is not deterministic

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

### Testing time

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

### Adversarial tests, which are not optional

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

### Failure injection

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

### Test data

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

### What cannot be unit tested

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

### Per-phase gates

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

### Requirements

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


---

## Technology Choices

Everything in the design docs is stated in **roles**. This is where roles meet products. Keeping
the two separate is not pedantry: the same design runs on three very different stacks, and a role
that names a product cannot be re-filled.

### Role → implementation

| Role | Chosen | Viable alternatives | Swap cost |
|------|--------|--------------------|-----------|
| `record store` | Postgres 16 | any mature RDBMS | **high** — system of record |
| `vector index` | pgvector, inside the record store | Qdrant, Weaviate, Pinecone | medium |
| `lexical index` | Postgres `tsvector` | OpenSearch, Elasticsearch | medium |
| `data access` | Kong + PostgREST via `supabase-py` | raw psycopg | **high** — `storage.py` is ~6,400 lines |
| `blob store` | GCS | S3, Azure Blob, filesystem | **low** — abstracted by `STORAGE_BACKEND` |
| `durable queue` | NATS JetStream / **Pub/Sub in cloud** | Kafka, SQS, Redis Streams | medium — **already swapped per variant** |
| `temporal graph store` | Neo4j + Graphiti | FalkorDB, Memgraph, none | **low** — gated by `is_graphiti_enabled()` |
| `credential broker` | Nango, self-hosted | Paragon, Merge, custom | medium |
| `inference layer` | Ollama / Ollama Cloud + Gemini | any OpenAI-compatible endpoint | **low** — Model Garden abstracts it |
| `identity provider` | GoTrue → Firebase / local | any OIDC provider | **high today** (hardcoded at two sites); **low after abstraction** |
| `orchestrator` | Kubernetes + KEDA / Cloud Run | ECS, Nomad | medium |

### The bet has a cost

Colocating the **vector index and lexical index inside the record store** is what makes "only two
required roles" possible — no extra infrastructure, no sync problem, joins and ACL filters in one
query. It is also why the capacity plan says *"Postgres is the shared fate"*: every workspace
competes for the same instance, and filtered ANN search over tens of millions of rows is the
load-bearing risk.

**The floor is low because the ceiling is shared.**

### Abstract the queue before you need to

NATS JetStream and Pub/Sub differ in ack deadlines, ordering guarantees and redelivery semantics.
The cloud variant already replaces one with the other, so the queue must sit behind an interface —
otherwise there are two ingestion paths to keep correct and the worker retry policy has to be
written twice.


---

## Deployment Variants

The variants are not scaled versions of each other — they make different technology choices and
therefore support different subsets of the use-case families.

### Role fulfilment by variant

| Role | Local | GKE (dev today) | Cloud (prod v1) |
|------|-------|-----------------|-----------------|
| `record store` | Postgres in compose | Supabase in-cluster | Cloud SQL PG16 + pgvector |
| `data access` | Kong + PostgREST | Kong + PostgREST | Kong + PostgREST on Cloud Run |
| `blob store` | filesystem | GCS | GCS |
| `durable queue` | **in-process** — one instance, nothing to distribute | NATS in-cluster | **Pub/Sub** |
| `inference` | **Ollama local** — free | Ollama pods, tiered | **Ollama Cloud + Gemini**, no GPU pool |
| `temporal graph` | off | Neo4j optional | **deferred** |
| `credential broker` | Nango in compose | Nango in-cluster | Nango on Cloud Run |
| `identity` | **local password** | GoTrue | **Firebase Auth** |
| `conversational agent` | optional | DigiMe in cluster | **cut** |
| `crawl scheduling` | in-process ticker | cluster CronJob + advisory lock | managed scheduler → HTTP |
| `crawl execution` | background task | Deployment | **Cloud Run Jobs** — a 3h backfill is not a request |
| `rate-limit store` | in-process | Redis pod | Memorystore |
| `secrets` | `.env` | k8s Secrets | Secret Manager |
| `scaling` | n/a | KEDA | Cloud Run, `min-instances ≥ 1` |

### The three

**Local** — laptop or Mac Mini. Single user or household. Docker Compose, local models, filesystem
blobs. **$0 recurring and genuinely air-gap capable** — the only variant that satisfies the privacy
pillar in full, and the only one that sidesteps the compliance apparatus entirely.

**GKE** — dev today. Self-hosted cluster across six namespaces, tiered inference pods, KEDA
autoscaling. Richest capability set — the only variant running every component at once.

**Cloud** — prod v1. Serverless: Cloud Run plus managed services, no cluster at all. Reverses the
dev topology. Two components were forcing a cluster; one was cut and the other verified
request-scoped.

### Which variant serves which use case

| Family | Local | GKE | Cloud | Note |
|--------|:-----:|:---:|:-----:|------|
| A · Personal memory | ● | ● | ○ | Cloud loses conversational access with the agent cut |
| B · Team memory | — | ● | ○ | Cloud defers the temporal graph, weakening institutional recall |
| C · Agent infrastructure | ● | ● | ● | MCP works everywhere |
| D · Embedded backend | — | ○ | ● | Cloud is the intended host-SaaS target |
| E · Governance | ○ | ○ | ● | Managed tier makes audit and residency tractable |

### Where security artifacts live

"Secrets go in Secret Manager" is too coarse. Five distinct classes with different requirements,
and conflating them is how a KEK ends up retrievable as a string.

| Artifact | Local | GKE | **Cloud (GCP)** |
|----------|-------|-----|-----------------|
| **Key-encryption key (KEK)** | file, dev-only | k8s Secret | **Cloud KMS** — never leaves |
| **Per-tenant data keys (DEK)** | wrapped, in the record store | same | same — wrapped by KMS, ciphertext in Cloud SQL |
| **Per-tenant secrets** — AI provider keys, webhook signing | envelope-encrypted in the record store | same | same — **not** Secret Manager |
| **OAuth tokens** | credential broker's own store | same | broker's Cloud SQL, AES-256-GCM |
| **Platform secrets** — broker encryption key, third-party platform keys | `.env` | k8s Secret | **Secret Manager** |
| **JWT signing key** | file | k8s Secret | **Cloud KMS asymmetric signing** |
| **`md_*` API keys** | hashed in the record store | same | same — hashed, never encrypted |
| **Policy and settings** | record store | same | Cloud SQL — not secrets |

#### Secret Manager and KMS are not interchangeable

**Secret Manager stores and returns a value.** Correct for something the application must hold —
the credential broker's encryption key, a platform-level third-party key.

**KMS performs cryptographic operations without releasing the key.** Correct for the KEK, because
the whole point of envelope encryption is that the key-encryption key never enters application
memory. Putting a KEK in Secret Manager gives you one string away from total compromise, which is
the situation envelope encryption exists to avoid.

Same reasoning for JWT signing: **KMS asymmetric signing** means the private key never exists in a
process, so a memory disclosure cannot forge tokens.

#### Per-tenant secrets do not belong in Secret Manager

Secret Manager is built for a bounded set of platform secrets, not one entry per tenant per
provider. Wrong quota model, wrong access model, and no way to scope reads per tenant.

Per-tenant secrets are **envelope-encrypted in the record store**: plaintext → tenant DEK → wrapped
by the KMS KEK → ciphertext in Cloud SQL. Rotation is a KMS key version bump plus a DEK re-wrap,
not a re-encrypt of the corpus.

#### Two credentials that should not exist at all

| Removed by | What it removes |
|-----------|-----------------|
| **Workload Identity** | Service account **key files**. Cloud Run services assume an identity; there is no key to leak, rotate or commit |
| **Cloud SQL IAM database authentication** | The database **password**. The service account authenticates directly |

Both matter given the launch blocker already on record — secrets committed to git history. The best
defence is a credential that does not exist.

Signed URLs still require the service account to hold `roles/iam.serviceAccountTokenCreator` **on
itself**, which is easy to miss and fails only at runtime.

#### Where the audit log physically lives

[Privacy foundations](#privacy-foundations) requires an append-only audit record on
every read, with retention in years. That is not Cloud Logging — wrong retention model, awkward for
"who accessed this record in March", and it is operational logging rather than a compliance
artifact.

**Write to Cloud SQL, export to BigQuery.**

- The write is **transactional with the read it records**, so no code path can skip it
- Immutability is enforced by the database: the application role holds `INSERT` and `SELECT` on the
  audit table and **no `UPDATE` or `DELETE` grant**. Immutable by permission, not by discipline
- Cloud SQL keeps a hot window; BigQuery holds the long tail cheaply and answers the year-scale
  question
- Separate from `tracing` memories entirely — those are sampled, three-day, and observability

### Two capability gaps worth naming

**Cloud cuts the conversational agent**, so "reach your memory from any messaging app" — a
headline capability and the only genuinely unique one — exists only on self-hosted variants.

**Cloud defers the temporal graph**, one of the two differentiated capabilities.

Prod v1 therefore ships without the two features that most distinguish the product. That may be
correct for a first release, but it should be a stated trade rather than an emergent one.

### Launch blockers on record

- **Committed secrets** in `k8s/nango/nango-secrets.yaml` and `k8s/lean/*` — present in git
  history, so **rotation** is required, not deletion
- **`minReplicaCount: 0`** across the autoscaling manifests, which fights any availability target
- **Gmail watch state persisted only to `/data`**, which does not survive beyond one volume — the
  dead `_WATCH_BLOB_KEY` constant shows blob storage was the original intent

### Verify before provisioning

Deploy the credential broker image to Cloud Run **in dev first**, to confirm the self-hosted build
tolerates a request-scoped lifecycle. It is the one component whose internals are outside our
control, and the cloud topology assumes it holds no background workers.



# Part VIII · The plan

*Sequence, decisions, and the market*


---

## Roadmap

### Sequencing principle

**Build a thin vertical slice that works end to end, then widen it.**

Prove write → store → read first. Once an item can be written and found, both halves of the system
are real and everything after is widening a spine that works, rather than integrating parts that
have never met.

```
1  SPINE          write → store → read            ← prove both halves
2  DEPTH          make what is stored good
3  CONNECTORS     the ingestion path
4  UPLOADS/BULK   the other producer shapes
5  CRAWLERS       scheduled pull
6  VARIANTS       local · cloud harden
7  TEAM & CASES
8  SCALE & COMPLIANCE
```

Each slice ends with something demonstrable. That is deliberate — the previous version had a large
foundation phase with nothing to show at the end, which creates pressure to skip parts of it.

#### Relationship to what already runs

The GKE deployment runs today with four connectors live. This plan does **not** stop it. The spine
is built alongside, and producers migrate onto it slice by slice — the existing gateway becomes a
producer in slice 3 rather than being rewritten in place.

That also changes the framing of the known bugs. The `is_downloaded` failure and the embedding
fallback are not "fixes to schedule" — the new write path is built with the content contract and
the embedding-model column from the first commit, so those defects have nowhere to exist.

---

> **Where to actually start** — the step-by-step build order to the first milestone is in
> [operations/implementation.md](#implementation-plan-stack).

### The plan at a glance

| Phase | Goal | Exits when |
|-------|------|-----------|
| **0 · Unblock** | Two independent urgencies | Secrets rotated, replicas above zero |
| **1 · Spine** | Write → store → read | An item is written into a project with an ACL, found by scoped search, cited, and the read is audited |
| **2 · Depth** | Make what is stored good | Items classify, summarise and entity-extract; changing a prompt can rebuild what the old one produced |
| **3 · Connectors** | Data arrives on its own | Connect a mailbox; mail ingests, enriches and is findable without an API call |
| **4 · Uploads &amp; bulk** | The other producer shapes | A 500 MB file ingests from a browser; ten thousand records land without stalling the platform |
| **5 · Crawlers** | Pull from what will not push | A connector backfills three years and stays current on a schedule |
| **6 · Variants** | Local and cloud harden | **Unplug the network and everything still works** |
| **7 · Team &amp; cases** | Sharing and correlation | A patient timeline assembles across sources, ordered by event time |
| **8 · Scale &amp; compliance** | Enterprise-addressable | Erasure completes and verifies; soak holds at target |

**Every exit is a test, not a demo** — see [operations/testing.md](#testing).

### Capabilities that span phases

| Capability | Lands | Note |
|-----------|-------|------|
| **Write-path telemetry** | **1** | You cannot tell the spine works if you cannot see it work |
| **Tenancy model** | **1** | Columns, not features. Retrofitting multi-tenancy is the expensive migration |
| **Observability, broader** | 2 → 8 | Detectors ship with the thing they watch, never after |
| **Domain event store** | 2 | The audit substrate must exist before events accumulate |
| **Token accounting** | 2 → 5 | Usage records with depth; estimate-vs-actual with crawler dry-run |
| **Model selection mechanism** | **1** | Engine registration and per-artifact provenance — not retrofittable |
| **Model catalog UX** | 6 | Cards, hardware feasibility, staleness-impact preview. Needs staleness fields (2) |
| **Trace context across the queue** | 1 | Span links, with the queue abstraction |
| **Deletion** | 1 → 8 | Single item in 1 · selector jobs and dry-run in 4 with the job machinery · entity refcounting and summary rebuild in 5 with reprocess · erasure, legal hold and verification in 8 |

---

### UI, placed by phase

Each slice ships the interface for what that slice made possible. There is no "UI phase", because
the parity requirement — everything settable in the UI is settable through the API — means **the UI
can never be ahead of the API**. That sequences it automatically.

| Slice | What the UI gains | Why then |
|-------|------------------|----------|
| **1 · Spine** | A **thin console**: write something, search, inspect a result | You cannot judge retrieval quality from a JSON body. One page, not a product |
| **2 · Depth** | Enrichment inspector — viewpoint, entities, classification layer reached; **prompt override editor with test-before-save and staleness preview** | You cannot tune a prompt without seeing what the last one produced, or what changing it invalidates |
| **3 · Connectors** | **Connect flows**, connection health, reauthorise | **On the critical path** — see below |
| **4 · Uploads** | Drag-and-drop, progress, per-item results | Uploads are inherently a browser feature |
| **5 · Crawlers** | **Config editor with dry-run preview**, run history, per-item errors | **On the critical path** — see below |
| **6 · Variants** | First-run setup, model picker with hardware feasibility | Local onboarding is the product's first impression |
| **7 · Team & cases** | Sharing, members, groups, **case timeline** | A timeline is inherently visual; a JSON timeline is not a timeline |
| **8 · Scale** | Admin console, audit search, public-share inventory, **deletion preview and confirmation** | The inventory catches a six-month-old mistake; the deletion preview stops one being made |

#### Two places the UI is genuinely blocking

**OAuth connect flows need a browser.** There is no API-only path to connecting Gmail — the user
must be redirected, consent, and return. Phase 3 does not ship without UI; it is *how you connect
at all*.

**Crawler dry-run is a safety mechanism whose value is visual.** A dry-run that returns JSON nobody
reads does not prevent the mistake it exists to prevent. "This will create 47,213 items, take six
hours and consume 84% of your monthly budget" only works if someone sees it. Shipping the crawler
without the preview UI removes the guardrail while keeping the feature.

Everywhere else the UI can lag by a slice without harm.

#### Relationship to the UI that exists

The running deployment has a working UI — dashboard, AI Studio, playground, settings. Same
strangler posture as the rest: it keeps serving the current system while the new console is built
against the new API. Convergence or retirement is a decision for around slice 3, once the connector
path has moved.

#### It is also a reference implementation

Hosts embedding the platform build their own surfaces, and the
[ephemeral-token pattern](#api-contract-surfaces) exists so they can do so without holding durable credentials.
Our UI is therefore the reference client as much as it is the product — which is a useful
discipline, because anything it can do only by reaching past the API is a bug in the API.

### Phase 0 — unblock

Independent of the spine. Neither blocks it; both are urgent on their own terms.

| Item | Why |
|------|-----|
| **Rotate committed secrets** | Present in git history — deletion does not fix exposure |
| `minReplicaCount ≥ 1` | The running deployment cannot meet any availability target at zero |

---

### Phase 1 — the spine

**Goal: write an item, find it by search, get it back.**

#### The rule that keeps this a slice and not a foundation phase

Several concerns are **high priority but not fully built here**. The distinction:

> **Phase 1 ships the column and the enforcement point. The surface follows later.**

A column cannot be backfilled truthfully — you cannot reconstruct which org owned a row, which
model embedded it, or who read it last March. An enforcement point cannot be retrofitted cheaply —
adding ACL filtering to every query path afterwards touches everything. **A UI can be built any
time.**

So each concern below appears twice: what must exist now, and what deliberately does not.

#### 1a · The write and read path

| Area | Work |
|------|------|
| **Write** | Producer registry · `POST /api/v1/write` · `items[]` · `207` · idempotency key |
| **Content** | `ContentRef` defined in full; only `Inline` implemented |
| **Commit** | Synchronous, durable, returns `data_id` and readiness state |
| **Async boundary** | Queue abstraction with the **in-process** implementation |
| **Index** | Chunk · embed · lexical index |
| **Read** | `GET /data/{id}` · `POST /api/v1/retrieve` — vector, lexical, hybrid |
| **Memories** | Type registry with TTL and expiry policy · many-to-many membership · both mapping directions |
| **Delete** | `DELETE /data/{id}` — single item, cascade over its own derived artifacts |
| **Auth** | `TokenVerifier` seam, API-key verifier behind it |

#### 1b · Write-time facts — column and enforcement only

| Concern | **Phase 1 — column + enforcement** | **Later — the surface** |
|---------|-----------------------------------|------------------------|
| **Tenancy** | `org_id` / `project_id` **populated** · membership with roles · **every query scoped** | Invite flows · role management UI · org switcher |
| **Access** | `shared_with` holds **principals** · `public` renamed **`org`** · groups table + query-time resolution · **derived artifacts inherit strictest source** | Groups UI · share links with expiry · public-share inventory · ethical walls |
| **Memories** `P0` | **Type = name + TTL + expiry policy**, definable · type **mutable**, not in the id · `default` type so nothing is orphaned · **`memory_key`** unique per (project, type), upsert · many-to-many membership, **mutable after write** (add/remove, single and by selector) · **`memory_links`** · both mapping directions, with **effective expiry computed, never stored** · **`orphan_delete`** expiry | Type editor · memory browser · compression · automatic promotion rules · derived correlation as a retrieval signal |
| **Privacy** | **Audit record written on every read** · **provenance as a source *list* on every derived artifact** · encryption at rest failing closed · classification flag at ingest | DSAR tooling · export · break-glass · access-history view · delete cascade |
| **Model config** | Engine registration, encrypted, failing closed · assignment per purpose · **`model_id` and `generator_version` recorded per artifact** | Curated catalog · model cards · hardware feasibility · staleness-impact preview |
| **Admin** | Platform grants **orthogonal** to org roles · admin sees metadata, **never content** · global unscoped key retired | Platform console · usage reporting · support tooling |
| **Settings** | Precedence user → project → org → platform, with **lock** semantics | Full settings surface · policy editor |
| **Telemetry** | `write.*` counters by producer and reason · **`producer.seconds_since_last_item`** · **`embed.distinct_models_per_index`** · ingest→searchable | Dashboards · alerting · full catalogue |

Eight rows, not forty-four items. Each left-hand cell is something that becomes a migration — or,
for audit, becomes *impossible* — if deferred. Each right-hand cell can be built against existing
data whenever it is wanted.

#### Exit

An SDK call writes an item **into a project, owned by an org, with an access level**; a semantic
search **scoped to that project** finds it; the response cites it; the read is **audited**; and the
row records **which model embedded it**.

Expressed as tests rather than a demo — see [operations/testing.md](#testing).

#### Deliberately absent

No enrichment agents. No gateway. No fetch worker. No uploads. No crawlers. No graph. No cases.
No normalization. No settings UI. No share links.

#### Two things built right rather than deferred

**The content contract from the start.** `ContentRef` is defined with all three cases even though
only `Inline` is implemented, so `Stored` and `Pending` slot in later without the enrichment side
ever learning to branch on provenance.

**Embeddings never fall back.** Defer-on-unavailable ships with the first embedding call, not after
a corpus has been contaminated.

---

### Phase 2 — depth

**Goal: make what is stored good.** The spine already proves it is findable.

| Work | Notes |
|------|-------|
| **W1 enrich worker** | Moves enrichment off the write path properly |
| **Classification cascade** | Six deterministic layers, LLM as layer 7 |
| Typed agents | Start with a handful, not forty |
| Entity extraction → graph layer 1 | Record store tables; no external graph yet |
| **Staleness fields + `generator_version` fingerprint** | `sha256(canonical_json(config))` |
| **W7 reprocess** | You will want to tune prompts on day two. Without this, tuning is write-only |
| Telemetry | `enrich.classification_layer` — measures the ~80% claim rather than asserting it |

**Exit:** written items are classified, summarised and entity-extracted; changing a prompt can
rebuild what the old one produced.

---

### Phase 3 — connectors

**Goal: data arrives on its own.** This is where the ingestion path is built.

| Work | Notes |
|------|-------|
| **Gateway as a producer** | Translator in front of the write API — it does not become a second write path |
| `whk_` producer type | Per-user webhook endpoints |
| **Connection scope → ACL inheritance** | Personal vs shared. The rule that reconciles personal and team memory |
| **W2 fetch worker + `Pending`** | Most events carry a pointer, not a payload |
| Credential proxy integration | The boundary: only W2 reaches it |
| **Tier-3 default path** | JSON records ingest with no per-provider code — this is what makes breadth cheap |
| Tier-1 adapters | Gmail, Slack, Drive — the three already live |
| W4 subscription renewal | Watch state in the record store, not on a volume |
| Telemetry | `producer.seconds_since_last_item` — a dead connection errors nowhere |

**Exit:** connect Gmail; mail arrives, enriches, and is findable without anyone calling an API.

---

### Phase 4 — uploads and bulk

Both are producer shapes the spine already anticipates.

| Work | Notes |
|------|-------|
| Presigned upload flow | `Stored` content; bytes never traverse the API |
| Local upload signer | Filesystem storage has no signer — same API shape, local token |
| MIME sniffing server-side | Declared type is a hint, never the router |
| Bulk writes at scale | Same verb, more items; admission control on queue depth |
| `enrich: false` default for bulk | Full enrichment must be asked for and budgeted |
| **Selector-based deletion as a job** | Same run entity as bulk import — checkpointed, dry-run, per-item errors |

**Exit:** drag a 500 MB file into the UI and it ingests; push ten thousand records and the platform
stays responsive.

---

### Phase 5 — crawlers

**Goal: pull from what will not push.**

| Work | Notes |
|------|-------|
| `crw_` producer type · config store · dry-run gate | Created disabled; enabling requires a dry-run |
| Run lifecycle | Checkpointed, resumable, pause/resume/cancel |
| **`enumerate` strategy + backfill-on-connect** | Without backfill a new connection shows nothing until new data arrives |
| Three-layer dedupe | `external_id` · etag · content hash |
| **W8 mutate** | Crawlers re-discover constantly; without it every re-crawl duplicates or leaves stale facts |
| Per-provider and per-host limits | Bulk priority never starves live ingestion |
| Then: `query`, `tree`, `feed`, `search` | Templated HTTP covers most REST APIs without adapters |
| Later: `traverse` | Web crawling adds politeness and scope-escape risk — ship it last |

**Exit:** a Salesforce crawler backfills three years and keeps itself current on a schedule.

---

### Phase 6 — variants

The seams from Phase 1 are what make this cheap rather than a fork.

| Variant | Work | Exit |
|---------|------|------|
| **Local** | Lean compose · local password auth behind the existing `TokenVerifier` seam · **model catalog UX** on top of Phase 1's selection mechanism | **Unplug the network and everything still works** |
| **Cloud** | Managed queue behind the existing abstraction · hosted auth as another verifier · pooling audit · Secret Manager · crawl runs as jobs | Single-tenant prod, SaaS-ready |

**Gate:** run the credential-broker request-scoped-lifecycle spike before committing to the cloud
topology. Four more spikes are in [operations/implementation.md](#implementation-plan-stack); one
could *remove* work — the broker's own sync engine may replace part of Phase 5.

---

### Phase 7 — team and cases

| Work | Depends on |
|------|-----------|
| Sharing surface, invite flows, role management UI | Tenancy model (1) · connection ACL (3) |
| **Permission sync from source systems** | The gap competitors already ship |
| Normalization: `identifiers[]`, `event_time` | Depth (2) |
| Case primitive, membership, correlation | Normalization + staleness |
| Case-scoped retrieval, timeline, similar-case | Case primitive |
| Tier-1 connector depth | Connectors (3) |

---

### Phase 8 — scale and compliance

Quotas before raising ceilings · SSO/SAML · SCIM · immutable audit log · **erasure with
verification** · legal hold and partial completion · composable retrieval primitives · soak to target
workspace count.

> **Design the delete-cascade hooks in Phase 2**, when derived artifacts first exist. Retrofitting
> provenance after compression has absorbed content is the expensive version.

---

> **Every phase gate is a test, not a demo.** See [operations/testing.md](#testing)
> for the per-phase gates and the invariant suite.

### Two sequencing risks

**Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it cuts the
conversational agent and defers the temporal graph, shipping without either differentiator into the
most crowded quadrant. Local is cheaper, proves the privacy claim, sidesteps the compliance
apparatus entirely, and is where the unique capability lives.

**Phase 1 looks small and carries a lot.** A write endpoint and a search endpoint is not an
impressive demo, and the slice also carries tenancy, access principals, audit, provenance, model
recording and write telemetry. The temptation is to cut those to reach the demo faster.

Apply the rule instead of cutting: ship the **column and the enforcement point**, defer the
**surface**. Cutting a surface costs a sprint later. Cutting a column costs a migration — and for
the audit record, costs a question that can never be answered.

---

### Decisions

#### Closed

| Question | Settled as |
|----------|-----------|
| Team RBAC in mem-dog, or delegated? | **mem-dog enforces natively**; the host model is one broad service principal |
| Global privacy default? | **None** — visibility is producer/connection-scoped |
| Fixed search modes or composable? | **Composable** |
| Can the delete cascade wait? | **Build late, design early** |
| Keep the global `API_KEY`? | **No** |
| Auth provider? | **Firebase for login; user-created API keys validated at the gateway; both resolved to one identity.** Air-gap is served by a local password verifier behind the same seam — so **air-gapped operation is a self-hosted capability, not a property of the hosted product** |
| Inbound authentication? | **The same user-created keys** where the provider can present one; signature or URL secret otherwise, declared per producer |
| Separate batch endpoint? | **No** — one write verb, `items[]` |

#### Still open

| Decision | Note |
|----------|------|

| **Materialisation policy** | Always store (recommended) / threshold / derived-only |
| **Default ACL for a team upload** | Private-by-default is consistent; users dragging into a *team* space often expect team visibility |
| **Is media in scope for v1?** | Transcription infrastructure, and the largest cost exposure of any format group |
| **Backfill depth on first connect** | 30 days / 1 year / everything |
| **Cross-project cases** | Spanning breaks the project isolation boundary everything else relies on |

---

Stack choices and per-variant realisation are in
[operations/implementation.md](#implementation-plan-stack).


---

## Implementation Plan & Stack

*Proposals to validate against the codebase, not settled decisions. This was written from the
design documents; dependency manifests and the pipeline's existing framework may already provide
several of these, or already have an established alternative in-repo.*

### Constraints

1. **No new languages.** Python 3.12 and TypeScript. The channel agent stays Node.
2. **No new infrastructure unless a role demands it.** Reach for Postgres before adding a service.
3. **Every choice must work in all three variants** — or be behind the interface that lets them
   differ.

That third constraint is the one that does the work. It is why the queue, the scheduler, the
object store and the identity provider all need interfaces, and why almost nothing else does.

---

### Component stack

| Component | Tech | Why this one |
|-----------|------|-------------|
| Content contract | Pydantic discriminated union (`Field(discriminator=...)`) | Closed sum type *with* runtime validation; `is_downloaded` as `computed_field` so it is structurally unsettable |
| MIME sniffing | `filetype` / `puremagic` | Pure-Python, no native dep in slim images. libmagic is more accurate where you can carry it |
| Queue abstraction | `Protocol` over `nats-py`, `google-cloud-pubsub`, and an in-process impl | The interface is small; the *semantics* differ — ack deadlines, ordering keys, redelivery |
| Fetch worker | `httpx` streaming + resumable upload | Bytes never buffer |
| Retry / backoff | `tenacity` | Composable, jitter built in |
| Rate limiting | Redis token bucket via Lua | Must be atomic **across replicas** — in-process limiters silently fail past one pod |
| Crawl scheduling | Postgres schedule table + `pg_try_advisory_lock` | Leader election with zero new infrastructure, identical across variants |
| Crawl state | Postgres (`crawl_runs`, `crawl_frontier`) | Durable and resumable — precisely what Redis is wrong for |
| robots.txt | `protego` | Handles crawl-delay and wildcards; stdlib `robotparser` does not |
| HTML parsing | `selectolax` | Substantially faster than BeautifulSoup at crawl volumes |
| Field mapping | `jmespath` | Well-specified and boring. Do not invent a DSL |
| Chunking | `semantic-text-splitter` | Standalone; avoids pulling a framework in for one function |
| Password hashing | `argon2-cffi` | Argon2id |
| Token verification | `PyJWT` RS256 + JWKS cache, behind a `TokenVerifier` Protocol | Local and OIDC implementations from one seam |
| Trace propagation | OTel `TraceContextTextMapPropagator` | Inject into message headers, extract in the worker — closes the queue-hop gap |
| Model catalog | Static YAML → Pydantic, merged with discovery | Same pattern as `nango_provider_meta.py`; air-gap friendly |
| Config UI forms | `react-hook-form` + `zod` | Crawler configs need schema-driven forms with live validation |
| Integration tests | `testcontainers` + `respx` | Real Postgres and queue in tests; mock HTTP at the boundary |

#### Three non-obvious calls

**API keys hash with SHA-256, not Argon2.** Passwords need a slow KDF because they are low-entropy
and human-chosen. API keys are high-entropy random tokens — a slow hash buys nothing and costs you
on every request. Different threat model, different primitive.

**`generator_version` is a fingerprint, not a number.**
`sha256(canonical_json({prompt, model_id, schema, parser_version, chunker_version}))`. Staleness
detection becomes a join rather than a manual bump someone forgets, and it catches changes nobody
thought to version.

**Postgres advisory locks instead of an orchestrator.** The obvious answer for crawl scheduling is
Temporal or Airflow. Both are real dependencies with their own operational burden, and Temporal in
particular is hard to justify in the local variant. A schedule table plus `pg_try_advisory_lock`
covers scheduling, leader election and resumability at target scale.

#### Deliberately not added

**Celery** — its worker model does not fit queue-driven asyncio, and you would run two queue
systems. **A separate vector DB** — pgvector-in-the-record-store *is* the architectural bet.
**Kafka** — the chosen brokers suffice. **Playwright** — ship `traverse` without JS rendering; add
it opt-in per config if real sites demand it. **A second graph database.**

---

### Per-variant realisation

| Concern | Local | GKE | GCP (Cloud Run) |
|---------|-------|-----|-----------------|
| Record store | Postgres container | Supabase in-cluster | Cloud SQL PG16 + pgvector |
| **Pooling** | direct, small pool | PgBouncer / pooler | **Cloud SQL connector + PgBouncer — mandatory** |
| Data access | Kong + PostgREST | Kong + PostgREST | Kong + PostgREST on Cloud Run |
| Queue | **in-process** (single instance) | NATS StatefulSet | Pub/Sub |
| Queue client | in-memory impl | `nats-py` | `google-cloud-pubsub` |
| Blob store | filesystem | GCS | GCS |
| **Presigned upload** | local signed-token endpoint | GCS signed URL | GCS signed URL via IAM `SignBlob` |
| Inference | Ollama container | Ollama pods, tiered | Ollama Cloud + Gemini |
| Scheduler | in-process ticker | CronJob + advisory lock | Cloud Scheduler → HTTP |
| **Crawl execution** | background task | Deployment | **Cloud Run Jobs** |
| Enrich / fetch workers | asyncio tasks | Deployments + KEDA | Cloud Run services on Pub/Sub push |
| Stream workers (W6) | n/a | **StatefulSet + sharding** | not supported — needs a cluster |
| Rate-limit store | in-process | Redis pod | Memorystore |
| Auth | local password | GoTrue → abstracted | Firebase Auth |
| Secrets | `.env` | k8s Secrets | Secret Manager |
| Autoscale | none | KEDA on queue depth | Cloud Run concurrency, `min-instances ≥ 1` |
| Telemetry sink | stdout / local collector | OTel → Prometheus + Grafana | Cloud Trace + Monitoring |

#### GCP — five things that will bite

**1. Connection exhaustion.** Cloud Run can spin up a hundred instances; each holding a pool of ten
is a thousand connections against a Cloud SQL instance that allows far fewer. This is *the* classic
serverless-plus-Postgres failure. Small pools, lazy initialisation, and a pooler in front — not
optional.

**2. `/tmp` is memory.** Cloud Run's filesystem is in-memory and counts against the instance's
memory limit. Buffering a 500 MB download to disk does not degrade — it OOMs. Streaming straight
to the object store is **mandatory here**, where elsewhere it is merely correct.

**3. Ack deadline versus inference latency.** Pub/Sub push has a maximum ack deadline. A slow
enrichment call plus a fallback chain can exceed it, causing redelivery and duplicate work. Either
ack on receipt and track completion separately, or use pull subscriptions with `min-instances ≥ 1`.
Decide deliberately; the default will bite.

**4. Crawl runs are jobs, not requests.** A three-hour backfill does not fit a request-driven
service. Cloud Run **Jobs** are the right primitive — task-based, long-running, resumable via the
checkpoint in Postgres. Enrich and fetch stay as push-driven services.

**5. Signed URLs need a signer.** The service account needs
`roles/iam.serviceAccountTokenCreator` on itself to sign without a key file. Easy to miss, fails
only at runtime.

#### Local — three things that will bite

**1. Presigned uploads have no signer.** Filesystem storage cannot issue a presigned URL. Two
options: run MinIO for S3 parity, or keep the API shape and have the local backend return a URL
pointing at a local upload endpoint with a signed token. **Prefer the second** — same contract,
one fewer container, and the contract is what matters.

**2. The full stack does not fit a laptop.** Postgres, NATS, Redis, Ollama, Kong, PostgREST, Nango,
API and UI is a lot of memory before a model is loaded. Ship a **lean profile**: drop Nango (no
integrations), drop Redis (in-process limiting is correct at one instance), and run the **queue
in-process** — with one instance there is nothing to distribute. This is the third payoff of the
queue interface.

**3. Model choice is constrained by the machine.** This is where the catalog's hardware-feasibility
check earns its place: offering a 70B model to a 16GB laptop is a bad first experience.

#### GKE — two things that will bite

**1. Stream workers are not Deployments.** W6 holds one connection per account. A Deployment with
three replicas ingests everything three times. It needs a StatefulSet with account sharding and
leader election — a different deployment shape from every other worker.

**2. KEDA needs queue depth exposed.** Scaling enrich workers on CPU is wrong; they are blocked on
inference, not compute. Scale on consumer lag, which means the broker's metrics have to reach KEDA.

---

### Build order — where to actually start

The phases say *what*. This says *what you do on Monday*.

#### Step 0 — read the codebase

Everything in this document was derived from documentation. Nobody has opened `api/` or `webhook/`
while writing it, so several choices below will change on contact.

| Read | Because |
|------|---------|
| Dependency manifests | Several proposed libraries may already be present, or have an established in-repo alternative |
| `storage.py` and the PostgREST path | ~6,400 lines whose shape constrains the schema work |
| The two JWT verify sites in `main.py` | The `TokenVerifier` seam is a refactor of these, not a greenfield |
| The pipeline's ADK framework | It may already provide a worker or queue abstraction that would otherwise be duplicated |
| Existing migrations and table shapes | Determines whether Phase 1's schema is additive or a parallel set |

Roughly half a day. Do it before writing anything.

**In parallel and non-blocking: rotate the committed secrets.** They are in git history, that is
live exposure, and it has no dependency on the build order.

#### Steps 1–8 — to the first milestone

Order is dependency-driven; each step is unblocked by the one before it.

| # | Step | Why here |
|---|------|----------|
| **1** | **Schema** — orgs, projects, members, groups, **`identities`**, **`api_keys`**, producers (with `inbound_auth`), data items with `org_id`/`project_id`/`access_level`/principal `shared_with`, `derived_artifacts` with source **list** + `served_by_model` + `fallback_depth`, `generator_versions` registry, `audit_events`, embeddings with `model_id`/`dim` | Everything in Phase 1b is columns. Design them **once, together**. Highest-leverage single artifact in the plan — get it wrong and every later slice inherits a migration |
| **2** | **Crypto foundation** — KMS envelope encryption, encrypt/decrypt helpers, **failing closed** | Nothing can safely store a credential before this, and step 3 needs to |
| **3** | **Auth seam + producer registry** — `TokenVerifier` with the API-key verifier; `identities` and `api_keys` tables; capability scopes; producer `inbound_auth` | Every write needs a producer and a credential. Firebase and the gateway arrive later **behind this seam**, so building it now costs an implementation and skipping it costs a fork |
| **4** | **Model config, minimal** — one engine, one embedding assignment, `generator_versions` populated | Step 6 embeds. This is the difference between provenance from row one and a corpus-wide staleness event later |
| **5** | **Write endpoint** — `POST /write`, `Inline` only, `items[]`, `207`, commit, ACL from producer, **audit record written** | The spine's first half |
| **6** | **Embed path** — queue abstraction with the in-process implementation, `model_id` recorded, defer-never-fallback | Makes what was written findable |
| **7** | **Read** — `GET /data/{id}` and `POST /retrieve`, vector + lexical + hybrid, **ACL applied inside the query** | Closes the spine |
| **8** | **Invariant gate** — adversarial cross-tenant across every retrieval path, kill-after-`2xx` durability, `distinct(model_id) == 1` property | The exit criterion |

Tests are written **alongside** each step. Step 8 is the gate, not when testing begins — see
[testing.md](#testing).

#### The milestone

> Write an item into a project owned by an org with an access level → a search scoped to that
> project finds it → the response cites it → the read is audited → the row records which model
> embedded it.

At that point both halves of the system are real and every later slice widens a spine that works.

#### Nothing open blocks starting

Worth stating plainly, because it is easy to assume otherwise:

| Open decision | When it is needed |
|---------------|------------------|
| Own auth vs hosted IdP | Deferred by the `TokenVerifier` seam — Phase 6 |
| Materialisation policy | Phase 4, with uploads and fetch |
| Default ACL for a team upload | Phase 4 |
| Media in v1 scope | Phase 4–5 |
| Backfill depth on first connect | Phase 5 |
| Cross-project cases | Phase 7 |

**None of them gates step 1.** That is itself an argument for this ordering — the work that must
be decided last is also the work that happens last.

#### UI, in this window

Slice 1 needs a **thin console** — one page: write something, search, inspect a result. Not a
product surface. It exists because retrieval quality cannot be judged from a JSON body, and it is
built after step 7, against the API rather than beside it.

Full UI sequencing is in [the roadmap](#roadmap).

### Phased implementation

#### Phase 0 — correctness

**No new dependencies.** A Pydantic model change, two columns, a config value, and a rotation.

| Work | Tech |
|------|------|
| Content contract | Pydantic discriminated union; `computed_field` |
| MIME sniffing at ingest | `filetype` |
| `model_id` + `dim` on embeddings | migration |
| Disable embedding fallback | config + guard in the model client |
| Rotate committed secrets | `git-filter-repo` awareness — rotation, not history rewriting alone |

#### Phase 1 — foundation

The phase that introduces the interfaces.

| Work | Tech |
|------|------|
| Queue abstraction | `Protocol` + three impls (in-process, NATS, Pub/Sub) |
| Trace propagation across the hop | OTel propagator into message headers |
| `TokenVerifier` seam | `PyJWT`, JWKS cache via `httpx` + TTL |
| Asymmetric signing | RS256 keypair; serve JWKS |
| Worker split (W1/W2) | asyncio, `httpx` streaming, `tenacity` |
| Per-(provider, user) limits | Redis Lua token bucket — in-process impl for local |
| Staleness fields | Postgres tables; fingerprint via `sha256(canonical_json(...))` |
| Capability-scoped keys | FastAPI dependency; SHA-256 key hashes + prefix column |
| Classification-gated inference | Policy check before the fallback chain |

#### Phase 2 — demo (GKE)

| Work | Tech |
|------|------|
| Backfill-on-connect | `enumerate` strategy only; Postgres checkpoint |
| Crawl scheduling | schedule table + `pg_try_advisory_lock` |
| `minReplicas ≥ 1` | manifest change |
| Watch state → Postgres | migration off the volume |

#### Phase 3 — local MVP

| Work | Tech |
|------|------|
| Lean compose profile | in-process queue, no Redis, no Nango |
| Local password auth | `argon2-cffi`, lockout counters, reset tokens |
| Local upload signer | signed-token endpoint behind the same API shape |
| Model catalog + hardware check | static YAML → Pydantic; VRAM detection or declared |
| **Air-gap acceptance test** | disconnect network in CI; full flow must pass |

#### Phase 4 — cloud MVP

| Work | Tech |
|------|------|
| **Broker lifecycle spike** | deploy the credential broker to Cloud Run *in dev* first |
| Pub/Sub impl | `google-cloud-pubsub`, push subscriptions |
| Crawl runs as jobs | Cloud Run Jobs |
| Pooling | Cloud SQL connector + PgBouncer; audit pool sizes against max connections |
| Scheduler | Cloud Scheduler → authenticated HTTP |
| Secrets | Secret Manager |
| Signed URLs | IAM `SignBlob`; grant `serviceAccountTokenCreator` |
| Telemetry sink | OTel exporter → Cloud Trace / Monitoring |

#### Phase 4b — full crawlers

Remaining strategies (`query`, `tree`, `feed`, `search`, then `traverse`), dry-run, politeness via
`protego`, `selectolax` parsing, per-host concurrency, config UI with `react-hook-form` + `zod`.

#### Phases 5–6 — team, scale, compliance

Permission sync from source systems; SSO via OIDC behind the existing `TokenVerifier` seam; SCIM;
immutable audit log as a separate append-only store with real retention; delete cascade; k6 soak.

---

### Spikes to run before committing

| Spike | Question it answers | Blocks |
|-------|--------------------|--------|
| **Credential broker on Cloud Run** | Does the self-hosted image tolerate a request-scoped lifecycle? | Phase 4 topology |
| **Pub/Sub ack deadline vs enrichment p99** | Push or pull? | Phase 4 worker shape |
| **Cloud SQL connection ceiling under Cloud Run fan-out** | What pool size survives max instances? | Phase 4 sizing |
| **In-process queue parity** | Does the local impl satisfy the same contract tests? | Phase 3 |
| **Broker sync engine overlap** | Does the credential broker's own sync engine replace part of the crawler? | Phase 4b scope |

The last one could remove work rather than add it — worth running early.


---

## Competitive Landscape

**Researched:** August 2026 · **Status:** current

This supersedes the framing in the existing `docs/comparisons/` set (last updated March 2026),
which compares mem-dog only against the agent-memory category — mem0, Zep, BerryDB — plus one
connector platform and two data warehouses.

That framing has a structural problem: **it omits the two categories that compete most directly
for mem-dog's team and personal use cases.** Enterprise search (Onyx, Glean) and local-first
personal AI (Khoj, OpenClaw) are absent entirely, and Onyx in particular is the closest competitor
mem-dog has.

---

### Corrections to existing claims

Three claims in current documentation do not survive research.

#### 1. "Self-hosted and air-gapped" is not a differentiator

Repeated positioning treats private, air-gapped, $0 deployment as mem-dog's strongest moat.
It is table stakes in this category.

**Onyx** is MIT-licensed, ships 40+ connectors, and supports fully air-gapped deployment with
local models and zero internet connectivity — plus SOC 2 Type II, GDPR, SSO via OIDC/SAML,
SCIM, RBAC and full audit trails. It markets air-gapped operation as its defining capability
and targets defense, aerospace and regulated industries with it.

**Khoj** is an open-source self-hostable "second brain" running entirely on local models via
Ollama or llama.cpp.

Private-first is the price of entry, not the advantage.

#### 2. The connector ceiling is understated, not overstated

Documentation claims **300+ integrations**. Nango — the platform mem-dog delegates OAuth and the
provider catalog to — supports **900+ APIs**. The reachable ceiling is roughly triple what is
claimed, on what is arguably mem-dog's strongest axis. This is worth correcting in `index.mdx`,
`platform-overview.mdx` and the comparison set.

#### 3. Onyx is missing from the comparison set

For the team-memory use case, Onyx is the most directly competitive product in the market and
appears in no comparison document. See [comparison-onyx.md](#mem-dog-vs-onyx-detailed-comparison).

---

### Who competes for which use case

| Use case | Direct competitors | Nature of the threat |
|----------|-------------------|----------------------|
| **Personal memory** | Khoj, OpenClaw, Jan, Limitless | Free, permissively licensed, local-first, already shipping |
| **Team memory** | **Onyx**, Glean, GoSearch | Onyx is MIT and air-gapped; Glean has enterprise maturity and 100+ connectors |
| **Agent infrastructure** | Mem0, Zep, Letta, Cognee, Supermemory | Crowded and well funded; Mem0 has the widest integration surface |
| **Embedded backend** | Mem0, Zep, Supermemory | API-first by design — their home turf |
| **Governance** | Onyx, Glean | Already ship SOC 2, SCIM and audit trails |

---

### Feature matrix

`●` strong · `○` partial · `—` absent. Figures as researched August 2026; this category moves fast.

| Factor | mem-dog | Onyx | Glean | Mem0 | Zep | Cognee | Khoj |
|--------|---------|------|-------|------|-----|--------|------|
| Category | memory + search | ent. search | ent. search | agent memory | agent memory | agent memory | personal |
| **License** | proprietary | MIT | closed | Apache 2.0 | OSS core | OSS | OSS |
| Self-host | ● | ● | — | ● | ● | ● | ● |
| Air-gapped | ● | ● | — | ○ | ○ | ○ | ● |
| **Connectors** | 300+ (900+ reachable) | 40+ | 100+ | — | — | — | few |
| Ingestion mode | channels + connectors + API | connectors | connectors | SDK only | SDK only | SDK only | files + web |
| **Messaging channels** | ● 25+ | — | — | — | — | — | ○ |
| Typed memory + TTL | ● 10 types | — | — | ○ scopes | ○ implicit | ○ | — |
| Temporal knowledge graph | ○ optional | ○ LLM KG | — | ○ graph tier | ● native | ● | — |
| **Permission-aware retrieval** | ○ designed | ● ACL sync, pre-filter | ● | ○ scoping | ○ scoping | ○ | — |
| Multi-modal (audio/video) | ○ planned | ○ | ○ | — | — | ○ | — |
| Typed enrichment agents | ● 40 | — | — | — | — | ○ ECL | — |
| MCP server | ● | ● | ○ | ● | ● | ● | ○ |
| **SSO / SCIM / audit** | — | ● SOC 2, SAML, SCIM | ● | ○ ent. tier | ○ ent. tier | — | — |
| Entry price | $0 self-host | $0 community | $50+/user/mo | free tier | free tier | $0 self-host | free |
| Paid tier | — | $20/user/mo | $50+/user/mo | $249/mo graph | $125/mo flex | $200/mo · 10 users | paid cloud |

---

### Honest scorecard

#### Leads

| Factor | Against whom |
|--------|-------------|
| **Messaging-channel ingestion and conversational access** | Nobody else treats WhatsApp, Telegram, Signal and Slack as first-class memory channels |
| **Typed memory model** — 10 types, TTL, categories, versioning | mem0 has scopes; Zep is implicit; Onyx and Glean have no memory model at all |
| **Typed enrichment across 60+ data types** | Others index documents; none classify IoT, medical, geospatial or sensor data |
| **Connector breadth** | 2× Glean, 7× Onyx; the memory layers have none |

#### Ties

| Factor | Against whom |
|--------|-------------|
| Self-hosting and air-gap | Onyx and Khoj — both permissively licensed |
| Temporal knowledge graph | Zep — mem-dog runs Zep's own Graphiti engine |
| Search modes and reranking | Zep matches mode-for-mode and reranker-for-reranker |
| MCP tool surface | Everyone ships one now |

#### Trails

| Factor | Against whom |
|--------|-------------|
| **Permission-aware retrieval** | Onyx syncs ACLs from source systems and filters pre-retrieval — shipped, while mem-dog's is designed |
| **Enterprise compliance** — SOC 2, SSO, SCIM, audit | Onyx and Glean both ship it; mem-dog has none |
| **License** | Proprietary against MIT and Apache incumbents in every adjacent category |
| Ecosystem surface | Mem0 aligns with LangChain, CrewAI, AWS Agent SDK |
| Maturity and community | Mem0 ~50k GitHub stars; Onyx MIT with an active install base |

---

### Strategic read

mem-dog competes on **three fronts simultaneously** — personal memory, team search, agent memory —
against a specialist incumbent on each, while being **proprietary against permissively licensed
rivals**.

No single axis is defensible:

- Onyx matches the privacy and air-gap story under a more permissive license
- Zep owns the temporal graph engine mem-dog runs
- Mem0 owns the agent-integration surface
- Nango owns the connector catalog mem-dog delegates to

The defensible position is the **intersection**: connector breadth *and* memory semantics *and*
private deployment *and* conversational channel access. That combination is genuinely unoccupied.

But an intersection is only a moat if the combination is what customers buy. If they buy one axis
at a time, the intersection reads as three half-products competing with four full ones.

#### The finding that should change a decision

**Messaging-channel ingestion is the clearest differentiator — and it is cut from production v1.**

Per the v1 architecture decision, DigiMe/openclaw-node is removed from the serverless production
topology, and Graphiti/Neo4j is deferred. Cloud v1 therefore ships without *either* capability
that distinguishes mem-dog, landing it in the most crowded quadrant of the market with no
differentiation and a proprietary license.

Two further considerations:

- DigiMe is built on the **OpenClaw runtime, which is MIT-licensed and local-first**. The
  capability is replicable by anyone who chooses to build it.
- If channels are the moat, cutting them from the commercial variant while keeping them in the
  free self-hosted one inverts the usual monetisation logic.

#### What would strengthen the position

1. **Ship permission-aware retrieval properly** — it is the gap Onyx exploits, and the design
   already exists (connection-scoped ACL inheritance, query-time filtering).
2. **Decide the license question deliberately.** Competing proprietary against MIT in a
   developer-tools category is a choice with consequences; it should be made, not defaulted into.
3. **Reconsider cutting channels from v1**, or accept that v1 competes on price and breadth alone.
4. **Correct the 300+ figure to reflect the real 900+ ceiling** — it undersells the strongest axis.
5. **Enterprise compliance is the entry ticket** for the team use case. Without SSO and audit,
   Family B is not addressable regardless of feature parity.

---

### Documents

| Document | Covers |
|----------|--------|
| [comparison-onyx.md](#mem-dog-vs-onyx-detailed-comparison) | Onyx — the closest competitor; previously undocumented |
| [comparison-glean.md](#mem-dog-vs-glean) | Glean — the category leader, and the market it structurally cannot serve |

Existing comparisons live in `docs/comparisons/` (mem0, Zep, BerryDB, Nango, Snowflake/Databricks)
and are dated March 2026. They need a refresh pass and the corrections listed above.

---

### Sources

- [Onyx — Open-Source Glean Alternatives for Enterprise Search (2026)](https://onyx.app/insights/glean-alternatives)
- [Onyx — Self-Hosted AI Platform for Enterprise](https://onyx.app/solutions/secure-ai)
- [GoSearch — Glean Alternatives Compared (2026)](https://www.gosearch.ai/blog/what-are-the-top-3-glean-alternatives-2026/)
- [MCP.Directory — Mem0 vs Letta vs Zep vs Cognee (2026)](https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026)
- [Mnemoverse — AI Memory Solutions, Q3 2026](https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3)
- [Cognee — Open-Source AI Memory Tools](https://www.cognee.ai/blog/guides/best-open-source-ai-memory-tools-for-llm-agents-and-developers)
- [The AI Agent Index — Cognee Review (2026)](https://theaiagentindex.com/agents/cognee)
- [Nango — Best Unified API Platforms (2026)](https://nango.dev/blog/best-unified-api/)
- [Ampersand — 10 Best Unified API Platforms (2026)](https://www.withampersand.com/blog/the-10-best-unified-api-platforms-in-2026)
- [Khoj — self-hostable AI second brain](https://github.com/khoj-ai/khoj)
- [Vellum — Best Personal AI Assistants with Memory (2026)](https://www.vellum.ai/blog/best-personal-ai-assistants-with-memory)


---

## mem-dog vs Onyx: Detailed Comparison

**Last updated:** August 2026

Onyx (formerly Danswer) is an MIT-licensed enterprise AI platform bundling 40+ connectors, hybrid
search over an OpenSearch-backed vector index, permission-aware retrieval, AI chat, multi-step deep
research and custom agents. It is the leading open-source alternative to Glean.

**Onyx is the most directly competitive product mem-dog faces for the team-memory use case, and
until now it appeared in no comparison document.** It is also the product that most directly
contests mem-dog's privacy positioning — it is self-hostable, air-gapped-capable, and permissively
licensed.

---

### At a Glance

| | mem-dog | Onyx |
|-|---------|------|
| **What it is** | Private AI memory platform — multi-channel ingestion, 40-agent enrichment, RAG query engine | Enterprise AI search and assistant over connected company data |
| **Focus** | End-to-end data lifecycle with a typed memory model | Search, chat and agents grounded in company knowledge |
| **Deployment** | Self-hosted (Docker, GKE, Mac Mini) + planned Cloud Run | Self-hosted (Docker Compose, Helm, Terraform) + cloud |
| **Air-gapped** | Yes, on self-hosted variants | Yes — marketed as the defining capability |
| **License** | Proprietary | **MIT** |
| **Pricing** | Free self-hosted | Free community, $20/user/mo cloud |
| **Compliance** | None | SOC 2 Type II, GDPR, SSO (OIDC/SAML), SCIM, RBAC, audit trails |

---

### Feature-by-Feature

#### Data ingestion

| Feature | mem-dog | Onyx |
|---------|---------|------|
| Connectors | 300+ documented, 900+ reachable via Nango | 40+ |
| **Messaging channels** | 25+ (WhatsApp, Telegram, Signal, Discord, Slack…) via DigiMe | None |
| Push ingestion | Per-user webhooks, real-time via queue | Connector sync |
| Direct upload | Text, file, URL, camera, voice, video | File upload |
| Data types | 60+ including IoT, medical, geospatial, sensor | Documents and text |
| Enrichment | 40 typed sub-agents, 6-layer classification, tiered model routing | Indexing, chunking, embedding, LLM knowledge graph |

**Verdict: mem-dog leads clearly.** Onyx ingests documents from business systems. mem-dog ingests
from business systems *and* messaging channels *and* arbitrary data types, with typed analysis per
type. The connector gap is roughly 7× before counting the untapped Nango catalog.

#### Permissions and access control

| Feature | mem-dog | Onyx |
|---------|---------|------|
| Per-item ACL | 4 levels + `shared_with` | Inherited from source systems |
| **Source ACL sync** | None — permissions set in mem-dog only | **Pulls ACLs from source**: private Slack channels, ACL'd Confluence spaces, private repos |
| Filter point | Designed for query-time; currently varies | **Pre-retrieval**, not at the chat layer |
| RBAC | 4 org roles | RBAC + SCIM provisioning |
| SSO | None | OIDC/SAML — Okta, Entra ID, AWS IAM |
| Audit | Tracing memories | Full audit trails |

**Verdict: Onyx leads decisively.** This is the sharpest gap in the comparison. Onyx solved a
problem mem-dog has not started: **when you ingest a private Slack channel, who should see it?**
Onyx answers by syncing the source system's ACLs and filtering before retrieval. mem-dog requires
permissions to be managed separately, which does not survive contact with real corpora — a
connector that ingests everything a user can see, and then exposes it to everyone in the org, is
a data leak by construction.

mem-dog's designed answer — connection-scoped ACL inheritance with query-time filtering — is the
right shape, but it is a design and theirs is shipped.

#### Memory model

| Feature | mem-dog | Onyx |
|---------|---------|------|
| Typed memories | 10 types across 4 categories | None — it is a search index |
| TTL / expiry | Per-type defaults, overridable | None |
| Versioning | Every mutation, with diffs | Re-index on change |
| Compression | LLM summarization with archive | None |
| Temporal facts | `valid_at` / `invalid_at` via Graphiti | LLM knowledge graph, non-temporal |

**Verdict: mem-dog leads.** Onyx has no memory abstraction — it indexes documents and searches
them. Conversation state, session scoping, decaying context and point-in-time queries have no
equivalent. For agent memory, Onyx is not a competitor at all; for team *knowledge*, the memory
model may matter less than search quality.

#### Search and retrieval

| Feature | mem-dog | Onyx |
|---------|---------|------|
| Vector search | pgvector | OpenSearch-backed |
| Keyword | Postgres `tsvector` BM25 | Hybrid built in |
| Graph | Graphiti BFS + semantic | LLM-built knowledge graph |
| Modes | 5 (vector, fts, hybrid, graph, full) | Hybrid + reranking |
| Rerankers | 4 (none, RRF, MMR, cross-encoder) | Reranking included |
| Temporal filtering | Yes, via Graphiti | No |
| Deep research | No | **Multi-step deep research** |
| Custom agents | Per-agent pipeline configs | **Custom agents with MCP tool use** |

**Verdict: roughly comparable, different strengths.** mem-dog has more retrieval modes and
temporal filtering. Onyx has multi-step deep research and user-definable agents — capabilities
mem-dog lacks entirely, and which are increasingly what buyers evaluate.

#### Deployment and operations

| Feature | mem-dog | Onyx |
|---------|---------|------|
| Docker Compose | Yes | Yes |
| Kubernetes | GKE manifests | **Official Helm chart** |
| IaC | Manual scripts | **Terraform for AWS / Azure / GCP** |
| Air-gapped | Yes | Yes, with local models |
| Managed cloud | Planned (Cloud Run) | Available |
| Local models | Ollama, tiered | Configurable — OpenAI, Anthropic, open weights |

**Verdict: Onyx leads on operational maturity.** Helm charts and Terraform modules for three clouds
against manual deploy scripts is a meaningful adoption difference for the exact buyer both products
target — the team that wants to self-host.

---

### Where each wins

#### Choose mem-dog when

- Data arrives from **messaging channels**, not just business systems
- You need a **typed memory model** — sessions, TTL, decaying context, point-in-time queries
- You ingest **non-document data**: sensor, medical, geospatial, IoT
- You need **connector breadth** beyond 40 sources
- You are building **agent memory**, where Onyx does not compete

#### Choose Onyx when

- You need **permission-aware search over existing company systems** today
- **Compliance is a requirement** — SOC 2, SSO, SCIM, audit
- **License matters** — MIT versus proprietary
- You want **deep research and custom agents** out of the box
- You want **operational maturity** — Helm, Terraform, an install base

---

### Assessment

Onyx is not a memory platform, and mem-dog is not an enterprise search product. They collide on the
team-memory use case, and on that ground **Onyx is currently stronger**: permission-aware retrieval,
compliance, licensing and deployment tooling all favour it, and those are precisely the criteria a
team evaluating self-hosted knowledge tooling applies.

mem-dog's advantages — channels, typed memory, data-type breadth, connector count — are real but
sit *outside* the evaluation criteria for that buyer. They matter enormously for personal memory
and agent infrastructure, where Onyx does not compete at all.

**The strategic implication:** do not compete with Onyx on enterprise knowledge search. It is ahead
on the axes that decide those deals, and it is free. Compete where it structurally cannot follow —
messaging channels, typed memory semantics, and data types outside the document world — and close
the permission gap because that one is a correctness issue regardless of competition.

---

### Sources

- [Onyx — Open-Source Glean Alternatives for Enterprise Search (2026)](https://onyx.app/insights/glean-alternatives)
- [Onyx — Self-Hosted AI Platform for Enterprise](https://onyx.app/solutions/secure-ai)
- [Onyx — Enterprise Search Tools 2026](https://onyx.app/insights/enterprise-search-tools-2026)
- [NeuralChainAI — Self-Hosted Enterprise Search: Onyx for Regulated Teams](https://neuralchainai.com/solutions/self-hosted-enterprise-search-ai/)
- [elest.io — Onyx: Free Open Source AI Platform with Connectors, Agents & Knowledge Base](https://blog.elest.io/onyx-free-open-source-ai-platform-with-connectors-agents-knowledge-base/)


---

## mem-dog vs Glean

**Last updated:** August 2026

Glean is an enterprise AI search platform — workplace search, a conversational assistant and
autonomous agents across 100+ business applications, with hybrid retrieval and a dual-graph
(enterprise + personal) architecture.

It is the category leader, and the comparison is more useful for what it says about *market
position* than about features, because the two products are sold to different people to solve
different problems.

### At a glance

| | mem-dog | Glean |
|-|---------|-------|
| **What it is** | A memory platform — ingest, enrich, remember, retrieve | Enterprise workplace search and agents |
| **Shape** | A backend you build on | A destination you go to |
| **Deployment** | Self-hosted, air-gappable, or hosted | **Cloud only** |
| **Pricing** | $0 self-hosted | **~$50–75/user/month**, ~100-seat minimum |
| **Entry cost** | Hardware | **~$60,000/year ACV floor**; enterprise contracts exceed $200,000 |
| **Sales** | Self-serve | Custom-quoted, direct sales, no published pricing |
| **Connectors** | 300+ documented, 900+ reachable | 100+, deep and permission-aware |
| **Compliance** | None yet | SOC 2 Type II, RBAC, SSO/SAML, AES-256, GDPR/CCPA |
| **Ops burden** | Self-hosted ops | **1.0 FTE platform admin at 1,000+ users** |

### The structural difference

**Glean is a product. mem-dog is a platform.**

Glean is where a knowledge worker goes to find something. It has an API, but the API is an
extension of the application. You cannot build your own product on Glean and have your users never
know it is there.

mem-dog is the opposite: the API *is* the product, the UI is a [reference client](#api-contract-surfaces), and
the host-embedding contract exists so somebody else's application can use it as a private memory
backend invisibly.

Those are not competing implementations of one idea. They are different layers.

### The market Glean cannot serve

A ~100-seat minimum and a ~$60,000 floor is not a pricing choice, it is a **structural boundary**.
Below it, Glean does not have a product — not an expensive one, none.

That leaves, entirely uncontested by Glean:

- Companies under 100 people
- Individuals and small teams
- Developers embedding memory into their own product
- Anyone who cannot send data to a vendor cloud — defence, health, legal, regulated finance,
  sovereignty-constrained public sector
- Anyone who wants to own the corpus rather than rent access to it

**That is where mem-dog competes**, and Glean's absence there is by design rather than oversight.

### Where Glean genuinely wins

Stated plainly, because a comparison that finds no losses is marketing.

| Factor | Why |
|--------|-----|
| **Permission-aware retrieval** | Glean mirrors source-system ACLs and filters on them. mem-dog's equivalent is designed and unbuilt — the single sharpest gap |
| **Connector depth** | 100 deep, permission-aware connectors beat 900 shallow ones for workplace search. Different bets, and theirs is right for their buyer |
| **Enterprise compliance** | SOC 2 Type II, SSO/SAML, RBAC shipped. mem-dog has none |
| **Agents for workflows** | Multi-step autonomous agents over enterprise data, in production |
| **Maturity** | Install base, references, an enterprise sales motion, and the ability to answer a security questionnaire |

### Where mem-dog is genuinely different

| Factor | Why it matters — and to whom |
|--------|------------------------------|
| **Self-hosting and air-gap** | A real differentiator here, unlike against [Onyx](#mem-dog-vs-onyx-detailed-comparison). Glean is cloud-only, so sovereignty-constrained buyers have no Glean option at all |
| **Embeddable** | A backend others build on. Glean cannot be white-labelled into someone's product |
| **Typed memory with lifecycle** | TTL, expiry policy, compression, promotion. Glean indexes documents; it does not *remember* with a lifecycle |
| **Temporal facts** | "What was true in March" as a first-class query, not a search over documents that happen to mention March |
| **Non-document data** | Sensor, medical, geospatial, structured records, messaging channels |
| **Cost at scale** | 500 seats is ~$300,000/year of Glean and hardware for mem-dog |

### One differentiator that does not apply here

**Messaging-channel ingestion is mem-dog's most distinctive capability and is close to irrelevant
to Glean's buyer.**

An enterprise does not want WhatsApp and Signal indexed — that is a compliance liability, not a
feature. The channel story matters enormously for personal memory and for prosumer use, and barely
at all in the market Glean occupies.

Worth being clear about, because it is easy to list a capability as an advantage without asking
whether the buyer wants it.

### Assessment

**Do not compete for Glean's buyer.** They are ahead on the criteria that decide those deals —
permission-aware retrieval, compliance, connector depth, references — and they have a sales motion
mem-dog does not.

Compete for the buyer Glean structurally cannot serve: **under 100 seats, sovereignty-constrained,
or building a product rather than buying a tool.** That is a large market and Glean has ceded it by
construction.

The one thing to take from Glean rather than compete with: **permission-aware retrieval is table
stakes for any team deployment**, and both Glean and Onyx ship it. It is a correctness requirement
before it is a competitive one — a connector that ingests everything a user can see and then
exposes it org-wide is a data leak regardless of who else does it better.

### Sources

- [Coworker AI — Glean pricing, costs and TCO breakdown (2026)](https://coworker.ai/blog/glean-pricing)
- [GoSearch — Glean enterprise search pricing explained](https://www.gosearch.ai/faqs/glean-enterprise-search-pricing-explained-costs-tiers-hidden-fees-gosearch-comparison/)
- [Vendr — Glean software pricing and plans 2026](https://www.vendr.com/marketplace/glean)
- [checkthat.ai — Glean pricing 2026, costs, plans and ROI](https://checkthat.ai/brands/glean/pricing)
- [Glean — Comparing costs scaling AI search solutions in 2026](https://www.glean.com/perspectives/comparing-costs-scaling-ai-search-solutions-in-2026)
