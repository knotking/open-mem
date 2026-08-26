# Building mem-dog: Notes on What AI Memory Actually Requires

*A long read about ingestion pipelines, silent failures, vector spaces that don't line up,
and why the interesting problems in AI memory are almost never the AI.*

---

## The demo always works

Here is a thing you can build in an afternoon.

Point a script at a folder of PDFs. Chunk them. Embed the chunks. Stick the vectors in a database.
When a question comes in, embed the question, find the nearest chunks, paste them into a prompt,
and let the model answer. Add citation markers if you're feeling thorough.

It works. It works *impressively*. You ask "what did we decide about pricing?" and it finds the
paragraph from the meeting notes and quotes it back. Everyone in the room nods. Someone says the
word "moat."

Then you try to use it.

You realise the meeting notes are in Google Docs, not the folder. The pricing discussion actually
happened in Slack. The customer's objection that started the whole thing was an email. The revised
number is in a spreadsheet someone shared last Tuesday. The folder of PDFs was never where your
memory lived — it was just the part that happened to be easy.

This is the gap. Not between a good model and a bad model. Between a **demo** and a **system**.

Everything below is about that gap: what we found in it, what broke, and what we'd tell someone
starting over.

---

## Memory is seven problems wearing a trench coat

When people say "AI memory" they usually mean retrieval. Retrieval is maybe fifteen percent of it.
The actual list:

**Getting data in.** From forty applications, most of which don't want to give it to you, half of
which have no webhooks, and all of which have different ideas about what a "message" is.

**Understanding it.** A PDF is not a CSV is not a voice note is not a Salesforce opportunity. They
need different treatment and you cannot afford to send all of them to a large model.

**Structuring it.** "John Smith" in your CRM and "J. Smith" in an email thread and "John" in Slack
are one person. Something has to know that.

**Indexing it.** Not one index — several. The thing that finds documents by semantic similarity is
not the thing that answers "which invoices are over ten thousand" and neither is the thing that
answers "who was the CEO in 2024."

**Retrieving it.** With the right scope, the right ranking, and — critically — only the parts the
person asking is allowed to see.

**Keeping it current.** Documents get revised. Prompts get changed. Models get upgraded. Every one
of those makes some part of what you've built quietly wrong.

**Keeping it private.** Which turns out not to be a feature you add but a shape the whole system
has to be built in.

Six of those seven have nothing to do with the model. That ratio is the whole story.

---

## The bug that explains everything

Let me start with a specific failure, because it taught us more than any amount of design did.

Gmail and Google Drive were connected. Data was flowing. The dashboards were green — ingestion
succeeded, items were created, no errors anywhere. And yet searching for content from either
source returned nothing useful.

The items existed. They were just *empty of meaning*. No summary, no extracted entities, no
embedding worth having. They'd been ingested and never enriched.

The cause was a field called `is_downloaded`.

Here's the thing about Gmail: when a message arrives, the webhook doesn't contain the message. It
contains a `historyId` — a pointer. Same with Drive: a change notification carries a `fileId`, not
a file. Same with Slack file shares, Zoom recordings, almost everything. **The event tells you
something happened. It does not give you the thing.**

Our pipeline handled this with a boolean. If `is_downloaded` was true, the content was in the
envelope. If false, go download it from a URL. Reasonable enough.

Except that authenticated resources don't have a plain URL you can fetch. Gmail attachments need
OAuth. Drive files need OAuth. So the pipeline would dutifully look for a downloadable URL, not
find one, and fail with a message nobody was reading — while the ingest itself had already
returned `200 OK`.

And the deeper problem: `is_downloaded` was a field *callers set by hand*. It had to be manually
kept in sync with whether content was actually present. Which means, inevitably, it drifted.
Someone would attach content and forget the flag. Someone would set the flag on an envelope that
had only a reference.

Two sibling bugs had the same shape. A `source_type` of `DOCUMENT` with a MIME type of
`text/plain` would route to the PDF agent — because the code trusted `source_type` over the MIME
type, and `source_type` was also caller-supplied. And attachment envelopes needed `is_downloaded`
set correctly by hand, which the team's own notes described as "easy to miss."

Three bugs. One root cause: **the enrichment agents were making decisions based on fields that
could lie.**

### The fix is a type, not a check

The instinct is to add validation. Check that `is_downloaded` matches reality; log a warning when
it doesn't. That's treating a design problem as a hygiene problem.

The actual fix is to make the lie unrepresentable:

```
ContentRef =
  │ Inline (text | bytes)
  │ Stored (storage_ref, mime_type, size, checksum)
  │ Pending(provider, resource_id, hints)
```

Content is either *here*, or it's *in the object store*, or it's *a reference nobody has resolved
yet*. There is no fourth state, and no way to claim one state while being in another.
`is_downloaded` stops being a field anyone sets and becomes a derived property — computed from
whether content is actually present.

Routing keys off the MIME type, sniffed server-side. `source_type` is demoted to a hint. A caller
can still be wrong about it; being wrong just stops mattering.

The result: an enrichment agent **cannot tell how content arrived.** An uploaded PDF, a
Drive-fetched PDF and a PDF that came through a webhook are the same two-case type by the time any
agent sees them. The entire class of bugs becomes unrepresentable rather than merely unlikely.

This turns out to be a pattern we hit over and over. Most of the good decisions in this system are
of the form *remove the ability to express the wrong thing*, and most of the bad ones were *add a
field and trust everyone to set it right*.

---

## Push, pull, and the workers that don't get along

Once you accept that events carry pointers rather than payloads, the ingestion pipeline splits in
a way that isn't obvious up front.

Something has to *fetch* the thing the pointer points at. Something else has to *enrich* it. The
temptation is to make those the same worker — it's all "processing," after all.

They have opposite personalities:

```
FETCH                              ENRICH
─────                              ──────
waiting on a network socket        waiting on an inference call
cheap per task, run fifty          expensive per task, run three
throttled by someone else's API    throttled by your own model capacity
a retry costs bandwidth            a retry costs money
holds OAuth credentials            holds none
```

Put them in one pool and you get head-of-line blocking in both directions. A two-hundred-megabyte
Zoom recording downloading over a slow link occupies a worker slot that could be running
inference — doing nothing but waiting on a socket. Meanwhile a provider that rate-limits you to
five requests per second throttles your entire enrichment throughput, because the same workers are
stuck in backoff.

Two pools, two queues, scaled independently. Obvious in hindsight; not obvious while you're
writing it.

### The credential boundary

There's a second reason to split them, and it's the one that actually matters.

The enrichment pipeline is where LLM-generated logic runs. It's the component processing untrusted
content with a model, following instructions that partly came from the data itself. It is the last
component in your system that should be holding OAuth tokens for forty of your customer's business
applications.

So it doesn't. The fetch worker calls a credential-injecting proxy; the proxy holds the secrets
and adds them to the outbound request. The enrichment worker receives content and no credentials
whatsoever.

We had to correct ourselves on this once. An early draft of the architecture proudly labelled the
enrichment worker "zero credentials." That's not true, and the imprecision matters. There are two
credential *classes*:

- **Integration credentials** — OAuth tokens for Gmail, Slack, Salesforce. The enrichment worker
  must never have these.
- **AI provider credentials** — the API key for whatever model is doing the enriching. The worker
  currently must have these, or it can't call anything.

The invariant is narrower than we first claimed. And the fix for the second class is the same
pattern as the first: put an LLM proxy in front, inject credentials there, and let workers hold
nothing at all. Symmetry, arrived at embarrassingly late.

---

## The half of ingestion that doesn't arrive

Everything so far assumes data announces itself. A webhook fires. A user uploads. An SDK calls.

Most data doesn't do that.

A Salesforce org has three years of opportunities that nobody is going to re-save just to trigger
a webhook. A documentation site has four hundred pages and no notification mechanism. Review
platforms — G2, Capterra, Trustpilot, App Store — have no push mechanism at all, by design; they
are not interested in streaming their content to you.

So you need to go and get it. Which means crawlers.

The interesting thing about adding crawlers is that it made the design **smaller**.

We had, at that point, worked out a taxonomy of nine worker classes. Two of them were "backfill"
(pull the historical archive when a connector is first set up) and "poll" (check periodically for
sources with no webhooks). Adding a third pull-shaped worker for web crawling seemed like it would
make ten.

It made eight. Because backfill and poll aren't distinct things — they're **two schedules of the
same machinery**. A backfill is a crawl with full-history scope, run once. A poll is a crawl with
watermark scope, run on an interval. A web crawl is the same worker with link traversal as its
discovery strategy instead of API pagination.

One configurable worker. Six discovery strategies:

| Strategy | Enumerates by | Example |
|----------|--------------|---------|
| `enumerate` | Paginating a collection | Salesforce opportunities, Jira issues |
| `query` | Running a query on a schedule | Warehouse table, SOQL, saved search |
| `traverse` | Following links from seeds | Website, wiki, docs site |
| `tree` | Walking a hierarchy | Drive folder, S3 prefix |
| `feed` | Reading an index | RSS, sitemap, changelog |
| `search` | Repeating a query, collecting results | Review platforms, social |

And the crawler does something narrower than you'd expect: **it discovers and emits. It does not
fetch, and it does not enrich.**

It figures out what exists, checks whether it's seen it before, and drops a job into the fetch
queue or writes a record through the ordinary API. Everything downstream is machinery that already
exists. A crawled Salesforce record and a webhook-delivered one are indistinguishable by the time
enrichment sees them — which is the same property the content contract gave us, arriving from a
different direction.

It also means a crawler is, architecturally, just an API client. Which means it can run outside
your cluster entirely, and a customer could write their own.

### Crawlers will happily destroy you

A configurable crawler with a loose URL pattern will ingest the public internet on your customer's
inference budget. Cheerfully. Overnight.

So a few things stop being optional:

**Dry run.** Every config must be runnable in a mode that enumerates, reports counts, and fetches
nothing, writes nothing, spends nothing. Before it is ever scheduled. This is not a nice-to-have;
it's the only way to know what a `traverse` config will actually do before it does it.

**Change detection at three layers.** Without it, a nightly crawl of fifty thousand Salesforce
records re-embeds fifty thousand records every night, forever. So: `external_id` upsert so a
re-crawl updates rather than duplicates; etag or last-modified so you can skip *before* fetching;
content hash so you can skip enrichment even when metadata moved but bytes didn't.

**Politeness, for web specifically.** Honour `robots.txt` and crawl-delay. Identify yourself in
the user-agent with a contact URL, so an operator who's unhappy can reach you rather than just
blackholing your traffic. Cap per-host concurrency. And use an allowlist for scope, never a
blocklist — link traversal escapes any blocklist eventually, because that's what link traversal
*is*.

**Bulk runs at lower priority than live ingestion.** A customer's three-year historical import
must never starve the messages arriving right now.

### On agentic crawlers

The obvious next thought is: what if the crawler were an agent? Let it figure out pagination on an
undocumented API. Let it decide what looks worth following.

We landed on: **propose, don't execute.**

An agent that chooses its own frontier at runtime is a loop with a budget attached and no way to
predict what it will do. It's nondeterministic, so two runs of the same config produce different
corpora. It's unbudgetable, because you can't estimate what you can't enumerate. And it's
impossible to dry-run, which loses the one safety mechanism that actually works.

But an agent that reads an unfamiliar API's documentation and *writes you a crawler config* — with
strategy, scope, pagination and field mapping filled in, for a human to review before it runs —
is genuinely useful and completely safe. The agent does the understanding; the declarative worker
does the executing.

This became a recurring pattern. Wherever we wanted intelligence in the loop, the safe shape was
the same: **agents author configuration; humans approve it; deterministic machinery runs it.**
We use exactly that for field mappings too.

---

## The LLM's actual job is not summarising

Here's a reframe that changed how we thought about the pipeline.

The obvious use of an LLM in a memory system is to summarise things. Read the document, write a
précis, store the précis. That's what "enrichment" sounds like it means.

It's the wrong frame. **The LLM's job is manufacturing index keys.**

A classic inverted index maps terms that *appear in* a document to that document. Search for
"performance regression" and you find documents containing the words "performance regression." An
LLM lets you build an inverted index over vocabulary the document never contains.

Consider a support ticket that reads, in full:

> customer said it just spins forever after they hit save

That ticket should be findable by searching for *performance regression*. And *data loss risk*.
And *escalation candidate*. None of those phrases are in the text. No amount of clever embedding
of the literal words gets you there reliably, because the ticket and the query live in different
registers — one is how a support agent transcribes a phone call, the other is how an engineering
manager thinks.

Once you frame it that way, "what indexes should we build?" becomes a much more interesting
question than "should we summarise?"

The set we landed on:

| Index | Built by | Unlocks |
|-------|----------|---------|
| Chunk vectors | embedder | "things like this" |
| Lexical / BM25 | keyword index | exact terms, names, IDs |
| Entity + relationship graph | extractor | who connects to what |
| **Structural** | parser | precise citations — page, section path |
| **Normalized facets** | normalizer | `amount > 10000`, `status = open` |
| **Question index** | LLM | match question-to-question, not question-to-prose |
| Claim index | LLM | fact-level citation, contradiction detection |
| Concept index | LLM | inverted index over inferred concepts |
| Summary hierarchy | LLM | "what is this about" *and* "what's the specific number" |
| Intent index | LLM | decisions, action items, commitments |
| Document relations | LLM | supersedes / replies-to / cites |

Two observations about that list.

**The two cheapest are the most under-exploited.** Structural indexing — headings, tables, page
anchors — requires no inference at all; the parser already knows it, and it's what makes precise
citations possible instead of "somewhere in this 90-page PDF." Normalized facets are similarly
free: if you've already mapped a Salesforce record onto a canonical `Transaction`, then
`amount > 10000` is a SQL predicate, not a semantic search. Both are deterministic, both are
nearly free, and both were the last things we thought about.

**The highest-leverage LLM index is the question index.** Most RAG failure is a register
mismatch — people ask questions, documents make statements, and cosine similarity between a
question and a statement is weaker than between two questions. Generating "what questions does
this chunk answer?" and embedding *those* closes the gap directly, for one extra call per chunk.

### But you can't build all of them for everything

Eleven index types at up to one inference call each is eleven times the cost. A log line does not
need a question index. A contract does.

The control surface for this already existed and we didn't recognise it: the per-agent processing
flags — `extract_entities`, `extract_actions`, `extract_topics`, `embed`. Those aren't feature
toggles. They're **index-selection policy**, already per-user-configurable, already stored in the
database, already applied per invocation.

Layer a three-speed policy on top:

- **Eager** — deterministic indexes. Chunks, vectors, keyword, structure, facets. Always.
- **Deferred** — LLM-derived indexes. Questions, claims, concepts, summaries. On demand.
- **Adaptive** — build the expensive ones for content that actually gets retrieved.

That last tier is the interesting one. Most corpora have a long cold tail that nobody ever
queries. Building expensive indexes on first retrieval — or after the third — concentrates spend
on content that has demonstrated it matters. You pay with a slower first query on cold content,
which is nearly always an acceptable trade.

---

## Normalization, or: making entity extraction boring

There's a stage we initially skipped and had to add back: turning a provider's record into a
canonical object *before* enrichment runs.

Roughly sixty percent of a 900-provider catalog is JSON records. Stripe charges. Salesforce
contacts. Jira issues. Zendesk tickets. These aren't documents; they're structured data that
happens to arrive over HTTP.

If you send a Salesforce contact to an LLM and ask it to extract entities, it will correctly tell
you there's a person named Maria Chen who works at Northwind. That's an inference call, a few
hundred milliseconds, some tokens, and a small probability of getting it wrong.

If you map the record onto a canonical `Person` first, you *know* there's a person named Maria
Chen who works at Northwind. It's in the `Name` and `Account` fields. Zero inference, zero
latency, zero error rate.

**Normalization turns entity extraction from inferred to structural.** That's the deterministic-
before-probabilistic principle applied one layer up from where we'd been applying it.

The canonical types deliberately mirror the graph's existing entity types — `Person`,
`Organization`, `Message`, `Document`, `Event`, `Task`, `Transaction`, `Activity` — because a
mismatch between "what normalization produces" and "what the graph stores" creates permanent
translation loss at exactly the point where you least want it.

### Customization, and the mapping trap

Customers will need their own schemas. A recruiting company's `Person` has fields a law firm's
doesn't. So schemas are versioned records in the database, with precedence: project overrides org
overrides the built-in standard. Same precedence model as everything else in the tenancy system,
deliberately, because inventing a second one is how you get bugs at the seams.

The tempting shortcut is to let an LLM do the mapping at runtime. Point it at a payload and a
target schema and let it figure out the correspondence each time.

Don't. Runtime LLM mapping is nondeterministic — the same record normalizes differently on two
different days — expensive at scale, and impossible to debug. Instead: let an LLM *suggest* a
mapping from a sample payload, store the suggestion declaratively, let a human review it, and
execute it deterministically forever after.

Propose, don't execute. Again.

### Two rules that saved us

**Raw is truth; normalized is a derived view.** Never discard the original payload. Normalization
is lossy, your schemas will change, and your mappings have bugs you haven't found. Store the
projection alongside the original, tagged with the schema version that produced it, and treat it
as regenerable.

**Normalization failure must never lose data.** A record that doesn't fit the schema lands raw,
flagged `normalization_status = failed` with a reason attached, and stays retryable. A validation
error dropping an ingested item on the floor is the kind of bug you discover eighteen months later
when someone asks where their data went.

---

## The vector spaces that don't line up

This one is a genuine bug, not a design gap, and it's the most technically interesting thing we
found.

Every AI dependency in the system has a fallback chain. Local model unavailable? Try the cloud
one. Cloud one down? Try the third provider. This is good engineering — a provider outage degrades
quality, not availability.

The embedding path has a fallback chain too. Same pattern, same code, same idea.

**Embedding fallback is not a degradation. It is corruption.**

A vector produced by one embedding model and a vector produced by a different one live in
*different vector spaces*. Different dimensionality, different geometry, different meaning
assigned to every axis. Cosine similarity between them is a well-defined arithmetic operation that
produces a number, and that number is meaningless.

So: your primary embedder has a bad twenty minutes. The chain does what it was designed to do and
routes to the fallback. Four thousand documents get embedded into a different space and written
into the same index as everything else.

Nothing errors. Nothing logs. Ranking quality degrades in a way that's hard to attribute and
impossible to reproduce. And unless you happened to store which model produced each vector — which
we didn't — **you cannot identify the affected rows afterwards.** The corruption is unrecoverable
not because the data is gone but because you can't tell which data it is.

The fixes:

1. **Every embedding row carries `model_id` and `dim`.** Search filters to one space. Without this
   column you can't even measure the problem.
2. **Embeddings must not fall back.** If the primary embedder is unavailable, defer — mark the
   item `embed_status = pending` and try later. Slower is fine. Wrong is not.
3. **Changing embedding model is a corpus-wide migration.** Build the new index alongside the old,
   then swap. Never mix.

The general principle, which took us a while to articulate: **fallback is safe when outputs are
interchangeable and unsafe when they're not.** Two language models produce different words with
similar meaning — interchangeable enough. Two embedding models produce coordinates in incompatible
spaces — not interchangeable at all. The chain doesn't know the difference. You have to tell it.

---

## Everything derived goes stale

Once we'd been bitten by embeddings, the general shape of the problem became visible.

A derived artifact — an embedding, a summary, an extracted entity, a facet, a claim — is a
function of two things:

```
artifact = f(source_version, generator_version)
```

Change the source and it's stale. Change the *generator* and it's equally stale. And "generator
version" is compound in a way that's easy to underestimate: the agent's prompt, the model
identity, the model's *weights*, the output schema, the embedding model, the normalization schema,
the chunking strategy, and the parser. Different PDF parsers extract different text from the same
file. That's a generator change.

The system tracked neither axis. Which produces a specific, quiet failure: you tune a
summarization prompt in the UI, it takes effect immediately — and applies only to data ingested
*after* that moment. Your existing corpus keeps the old output forever. The feature works exactly
as documented and is nonetheless useless, because "improve the prompt" without "rebuild what the
old prompt produced" is only half an operation.

This is why a reprocess worker kept appearing as a prerequisite. It showed up three separate
times, from three different directions:

- Agent tuning needs it, or configuration changes are write-only
- Normalization schema evolution needs it, or schemas can never change
- Index generation needs it, or you can never add a new index type to old content

Three features, one missing dependency. It went from backlog item to critical path.

The concrete deliverable is a staleness model: every derived artifact records its source version,
its generator version, when it was produced, and a status. An artifact is stale when either
version moves. A sweep marks the affected rows and the reprocess worker rebuilds by priority.

Without that, "we changed the summarization prompt" means either re-running your entire corpus or
living with permanent inconsistency. At fifty million rows, the first option isn't available.

### Facts that are true forever

The knowledge graph has a subtler version of the same problem.

The temporal graph stamps facts with validity intervals — `valid_at` and `invalid_at`. That's how
you answer "who was the CEO in 2024" correctly even after the CEO changed. It's genuinely good
machinery.

But nothing sets `invalid_at` when a *source document is revised*.

So: a policy document says the approval threshold is $5,000. That fact goes in the graph, valid
from its ingestion date, `invalid_at` null — true indefinitely. Six months later the document is
revised to $10,000. The new fact gets extracted and stored. The old fact is still sitting there,
still marked valid, still with no end date.

Now someone asks what the approval threshold is. The system finds two facts, both valid,
contradicting each other — and confidently returns one of them.

**Confidently wrong is worse than empty.** An empty result makes someone go look it up. A wrong
result with a citation makes someone act on it.

There's a harder case behind it that we haven't solved: entity resolution decisions are themselves
claims that can be wrong. If the graph merges "John Smith" and "J. Smith" and later learns they're
two different people at two different companies, you need to un-merge — and merges are lossy. The
only tractable approach we can see is recording a merge as a retractable decision with its
evidence attached, rather than as a destructive edit. We haven't built it.

---

## Privacy is a shape, not a feature

The hardest design problem in the system wasn't technical. It was that two of our own use cases
wanted opposite defaults.

**Personal memory** wants everything indexed and everything surfaced. That's the whole point —
you connect your mail, your chat, your files, and you can find anything.

**Team memory** wants careful, deliberate sharing. Your colleague should find the shared project
docs. Your colleague should absolutely not find your personal email.

Now put them in the same product. A user connects their personal Gmail while a member of a team
organization. What should happen?

If visibility is a property of the *space*, you're stuck. Either the team space makes it visible —
which is a serious privacy failure — or the team space makes it private, which breaks the personal
use case for anyone who's also on a team.

The resolution took an embarrassingly long time to see:

> **Data inherits its ACL from the connection that produced it, not from the space it lands in.**

A connection carries a scope — `personal` or `shared` — chosen when it's created. Personal Gmail
connected inside a team org produces private items regardless of what the project's defaults are.
A team Slack workspace connected as shared produces member-visible items. Same organization, same
pipeline, opposite behaviour, no contradiction.

And it closed a security hole we'd been circling separately. The credential proxy takes a
`?user_id=` parameter and fetches that user's tokens. If authorization for that call is "are you
authenticated to this org," then an org admin can read a member's personal email through the
proxy. Once connections carry ownership and scope, proxy authorization becomes "do you own this
connection, or is it shared" — and the hole closes as a side effect of the privacy model rather
than as a separate patch.

### Derived data leaks too

Here's the part that's easy to get wrong at scale.

You have a private document. You extract twelve claims from it, three entities, a summary, a set
of concept keys, and forty embeddings. **Every one of those is derived from private content and
must inherit its privacy.**

Claims are the dangerous one, because claims are exactly the thing you most want to merge across a
corpus — that's what makes them useful. "Approval threshold is $10,000" is a much better index
entry than a chunk of prose. But if that claim came from a document only two people can see, it
cannot surface to a third.

Same for summaries. A compressed summary spanning items with different ACLs must take the
*intersection* — the most restrictive — or it leaks by construction.

Same for graph facts. The graph merges entities across sources; that's its job. A fact extracted
from your private document, surfaced to a teammate through entity traversal, is a data leak with
no audit trail and no error message.

Two rules, held firmly:

1. **Derived artifacts carry the ACL of their most restrictive source.**
2. **Retrieval filters in the query, never after ranking.** Post-filtering breaks top-K — you ask
   for ten results, filter down to three, and quietly return a worse answer. It also leaks
   existence, because the shape of what got removed is often inferable.

### The compliance flip

Somewhere in working through GDPR and HIPAA, the framing inverted on us.

We'd been treating self-hosting as the enthusiast tier — the thing hobbyists and paranoid
customers use, while the real business is the hosted product.

It's backwards. **Self-hosting doesn't just improve your privacy posture; it changes who the
regulated party is.**

| | Self-hosted | We host it |
|---|---|---|
| Our GDPR role | **Neither controller nor processor** — we never touch the data | Processor; DPA required |
| HIPAA | Customer is the covered entity; **no BAA needed from us** | We're a Business Associate; BAA required |
| Sub-processors | None, if inference is local | Cloud, inference, auth, credential broker |
| Cross-border transfer | None | Needs a transfer mechanism |
| Breach notification | Customer's obligation | Ours, on a clock |
| Certification burden | Effectively none | SOC 2 and audit evidence |

The self-hosted variant sidesteps the entire compliance apparatus. Not by being more secure — by
being architecturally outside the relationship that creates the obligations.

That's the strongest commercial argument for the local build, and it was sitting in the roadmap
labelled as the least important one.

### Two hazards we found in our own design

**The fallback chain crosses a legal boundary invisibly.**

Same chain as before: local model, then cloud, then third-party API. For availability, sensible.
For regulated content, it means PHI or personal data is **automatically transmitted to a third
party** — no error, no prompt, no record of which items took which route.

Under HIPAA that's a disclosure to a party that may have no BAA. Under GDPR it's an undisclosed
transfer. Structurally it's the *identical* defect as the embedding fallback: an automatic
substitution that's safe for availability and unsafe for correctness. Only here the consequence is
legal rather than a ranking error.

The fix is the same shape too: data classification gates the chain. Regulated items pin to local
inference and **fail closed** rather than falling back. Every inference call records which
provider actually served it.

**Compression defeats erasure.**

Memory compression summarises many items into prose — good for storage, good for retrieval
quality. Then an erasure request arrives. You delete the source item.

Its content is still in the summary. And in the claims extracted from it. And in the concept keys,
and the graph facts.

A right-to-be-forgotten implementation that deletes the row and leaves the substance in derived
text has not erased anything. This is why the delete cascade can't be deferred: it gets
exponentially more expensive once a production corpus has compressed mixed-subject content
together. It's cheap to design now and brutal to retrofit.

And a related realisation: **embeddings, extracted entities and graph facts are derived from
personal data and should be treated as personal data.** An erasure that removes a source row but
leaves its vector and its entity node has not completed.

### The agent nobody costed

While auditing this, someone noticed the pipeline ships a **Medical / DICOM agent**.

That's not incidental. It's an explicit design decision to ingest and analyse protected health
information — and nothing in the system addressed it. No BAA path. No audit controls. No
minimum-necessary enforcement. No documented encryption of content at rest. And an inference
fallback chain that can hand PHI to a third party at 3am because the local GPU was busy.

In a hosted deployment, HIPAA needs a BAA with *every* sub-processor that touches PHI, including
inference providers. Which likely means the current cloud inference stack can't be made compliant
at all. Either healthcare is a self-hosted-only story, or the inference choice has to change.

Shipping a DICOM agent is a compliance commitment. It's worth noticing that you've made one.

---

## The failure mode is silence

Here's a property of this system that took a while to name.

Almost every serious failure produces **no error**.

- A revoked OAuth connection stops ingesting. Returns nothing. Logs nothing. There's no error
  because there's no request.
- An embedding lands in the wrong vector space. Gets ranked anyway. Returns plausible results.
- An unenriched item is still searchable — just worse.
- A stale fact answers a temporal query confidently and incorrectly.
- A disabled webhook returns `200 OK` and drops the payload. On purpose.
- A crawler whose CSS selector broke runs successfully and discovers zero items — which looks
  exactly like "there was nothing new."

You cannot build observability for this system around error rates. There are no errors. You have
to build it around **detecting absence**.

The single most valuable signal we identified: **time since last item, per connection, compared
against that connection's own baseline.**

A Slack workspace that normally delivers two hundred messages a day and has delivered none for six
hours is broken. Nothing errored. No alert fired. No dashboard is red. And the user won't find out
until they search for something they know should be there and it isn't — at which point they don't
report a bug, they just quietly stop trusting the product.

Every other detector we came up with is narrower than that one.

A few more worth having:

- **Distinct `model_id` count per vector index** — must be exactly one. Any other value means the
  fallback bug happened.
- **Facts with `invalid_at = null` whose source has a newer version** — stale-fact detector.
- **Fallback depth reached** — a system running entirely on its third-choice model still *works*.
  It costs more, answers differently, and means the primary has been down long enough that nobody
  noticed. That's the difference between healthy and healthy-looking.
- **Crawler discovery count trending to zero** against its own baseline.
- **Explicit dropped-event counter** for disabled webhooks, because the alternative is an error
  rate that's correctly zero.

### Tracing stops where it matters

One structural gap worth flagging: request IDs propagate from the gateway through the API and then
stop at the queue boundary. The fetch and enrich workers produce spans that can't be correlated
back to the originating request.

Which means distributed tracing covers the fast, synchronous path that rarely fails, and none of
the slow, asynchronous path that does. "Why is this item unenriched?" is unanswerable.

Carry the trace context in the message envelope and restore it in the worker. It's a small change
that determines whether you can debug the half of the system where things actually go wrong.

---

## One design, three deployments

The system runs in three quite different configurations, and reconciling them produced the
discipline that ended up shaping the whole documentation set.

- **Local** — a laptop or a Mac Mini. Docker Compose, local models, files on disk. Zero recurring
  cost. Genuinely air-gappable.
- **Self-hosted cluster** — Kubernetes, tiered inference pods, autoscaling. The richest
  configuration; everything runs at once.
- **Serverless cloud** — managed services, no cluster at all. Different queue, different auth,
  different inference, two components cut entirely.

Early drafts of the architecture named products: "Postgres stores this," "NATS carries that." Then
someone pointed out that the requirements section was full of implementation.

They were right, and fixing it turned out to matter more than it sounded.

Rewritten in **roles** — `record store`, `durable queue`, `inference layer`, `credential broker`,
`temporal graph store` — the three variants stop being three architectures and become **one table
with three columns**. The cloud variant swaps the queue technology; that's a cell, not a redesign.

It also makes the dependency structure legible. Exactly two roles are non-negotiable: a record
store and the API surface. Everything else — the temporal graph, the credential broker, the
channel gateway, the conversational agent, even the inference layer — can be absent and the system
still stands, in a reduced form.

And it surfaces a metric that's genuinely useful for planning: **swap cost**.

| Role | Swap cost | Why |
|------|-----------|-----|
| Blob store | Low | Already behind an abstraction |
| Inference layer | Low | Model routing abstracts it |
| Temporal graph | Low | Optional by design |
| Durable queue | Medium | Already swapped per variant |
| **Identity provider** | **High today, low after abstraction** | Hardcoded at two call sites |
| Record store | High | System of record |

That identity row is the whole argument for doing the auth abstraction first, expressed as a
number rather than an opinion.

### The trap in the deployment order

The cloud variant is the revenue path, so it pulls hardest. Build that first, obviously.

Except the cloud variant cuts the conversational agent — the messaging-app integration that lets
you forward something to WhatsApp and ask about it a week later — and defers the temporal graph.

Those are the two most distinctive things the product does. So the commercial variant ships
without either differentiator, into the most crowded segment of the market.

Meanwhile the local variant is cheaper to build, proves the privacy claim in a way no amount of
documentation can, sidesteps the entire compliance apparatus, and is where the genuinely unique
capability actually lives.

That may still be the right call. But it should be a decision someone made, not a consequence
nobody noticed.

---

## What the market actually looks like

We wrote comparison documents. They compared against the AI-memory category: mem0, Zep, and
similar. Reasonable — that's the category the product describes itself as being in.

Then we went and looked properly, and found a competitor absent from every document.

**Onyx** (formerly Danswer) is MIT-licensed. It ships 40+ connectors, hybrid search, permission-
aware retrieval, AI chat and custom agents. It supports fully air-gapped deployment with local
models. It has SOC 2 Type II, SSO via OIDC/SAML, SCIM, RBAC and audit trails. It's free for the
community edition.

That forced a correction to something we'd been asserting confidently. We had "self-hosted,
air-gapped, $0" listed as a strong differentiator.

**It isn't. It's table stakes.** Onyx does it, under a more permissive license, with compliance
certifications we don't have. Khoj does a version of it for personal use. Private-first is the
price of entry in this category, not the moat.

Worse for our roadmap: Onyx already ships **permission-aware retrieval** — it syncs ACLs from
source systems (private Slack channels, restricted Confluence spaces, private repos) and filters
*before* retrieval rather than at the chat layer. That's precisely the architecture we'd carefully
designed. Shipped, versus designed.

Being honest about where that leaves things:

**Leads:** messaging-channel ingestion (nobody else treats chat apps as memory sources); a typed
memory model with TTLs and categories; typed enrichment across sixty-plus data types including
sensor, medical and geospatial; connector breadth.

**Ties:** self-hosting and air-gap; temporal knowledge graph (against a competitor whose engine we
run); search modes and rerankers.

**Trails:** permission-aware retrieval; enterprise compliance; license — proprietary against MIT
and Apache incumbents in every adjacent category; ecosystem integration surface.

The structural problem is that this competes on **three fronts at once** — personal memory, team
search, agent memory — against a specialist incumbent on each, while being proprietary against
permissively licensed rivals. No single axis is defensible. The competitor with the privacy story
has a better license. The competitor with the temporal graph *wrote* the engine. The competitor
with the agent ecosystem has the integrations.

The defensible position is the **intersection**: connector breadth *and* memory semantics *and*
private deployment *and* conversational channel access. That combination is genuinely unoccupied.
Nobody does connectors and memory and privacy together, because the connector work is the
expensive, unglamorous half that everyone skips.

But an intersection is only a moat if the combination is what people buy. If they buy one axis at
a time, an intersection reads as three half-products competing against four whole ones.

One genuinely good number, found while checking the others: the credential broker supports **900+
APIs**, not the 300+ the documentation claims. The connector ceiling is roughly triple what we'd
been telling people — an understatement of the single strongest axis.

---

## What we'd tell someone starting over

**Design so wrong states can't be expressed.** Most good decisions here were of the form "remove
the ability to represent the invalid thing." Most bad ones were "add a field and trust callers."
A boolean that must be manually kept in sync with another field will drift. A closed sum type
can't.

**Separate workers by resource profile, not by conceptual tidiness.** Anything I/O-bound and
anything inference-bound belong in different pools with different queues, different scaling and
different failure policy. Mixing them creates head-of-line blocking in both directions.

**Fallback is safe only when outputs are interchangeable.** Two language models produce different
words with similar meaning — fine. Two embedding models produce coordinates in incompatible spaces
— not fine. The chain can't tell the difference; you have to encode it. And "regulated content"
is another kind of non-interchangeability, with legal rather than numerical consequences.

**Every derived artifact needs both version axes.** Source version *and* generator version.
Without both, "is this stale?" is unanswerable, and every configuration knob you ship is
write-only for existing data.

**Build the reprocess path early.** It'll show up as a prerequisite for three unrelated features.
It did for us.

**Privacy is a shape, not a feature.** Deciding that data inherits its ACL from the connection
rather than the container resolved a contradiction we'd been treating as a product tradeoff. And
remember that everything derived — embeddings, claims, summaries, graph facts — inherits the
sensitivity of its source.

**Design telemetry around absence.** If your system's failures are silent, error rates tell you
nothing. "Time since last item, per source, against its own baseline" is worth more than a
dashboard of green checkmarks.

**Write requirements in roles, not products.** It seems pedantic until you have three deployment
variants, at which point it's the difference between one architecture and three.

**Agents propose; deterministic machinery executes.** Every place we wanted intelligence in the
loop, that shape was the safe one. Agent-authored crawler configs and field mappings, reviewed by
a human, executed by boring code. Agents choosing frontiers at runtime is a budget with a loop
attached.

**Look at your competitors properly before claiming a moat.** We spent a while believing
self-hosting was a differentiator. It's an entry requirement, and a well-funded MIT-licensed
competitor was already past it.

---

## The thing this is really about

There's a version of AI memory that's about models. Better embeddings, longer context, cleverer
retrieval. That version is well-served — a lot of smart people are working on it and it improves
every few months without anyone here doing anything.

The version that's actually hard is plumbing.

It's that Gmail sends you a `historyId` instead of a message. That Google Docs have no bytes to
download and must be exported. That HEIC is the iPhone default and needs a library that isn't in
your base image. That `mbox` exports are multi-gigabyte single files containing fifty thousand
messages. That WhatsApp voice notes arrive as `amr`. That a nightly crawl re-embeds everything
unless you thought about etags. That a summary written last month still contains the paragraph
someone asked you to delete.

None of that is interesting in the way a new model architecture is interesting. All of it is what
stands between a demo that impresses a room and a system someone actually keeps using six months
later.

The measure of a memory system isn't how good the answer is when it works. It's whether you can
still trust it after it's been running unattended for a year — after connections expired, models
changed, prompts got tuned, documents got revised, someone left the company, and someone else
asked to be forgotten.

That's a data lifecycle problem wearing an AI costume. We found it more interesting than we
expected.

---

*The full technical documentation — requirements, architecture, ingestion design, retrieval,
security and compliance analysis, deployment variants, roadmap and competitive assessment — lives
alongside this article in [`docs/`](../README.md).*
