# Use Cases

Five families, all in scope. Supporting every family means building to the **maximum** of each
dimension rather than the average: the strictest privacy requirement applies everywhere, the
tightest latency budget governs the eager path, the largest scale target governs capacity, and
the broadest connector catalog governs coverage.

## What the indexes enable

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

## What this actually looks like

Six concrete scenarios. Each names **what makes it work** — because most of the machinery in these
documents exists for a specific reason, and this is where those reasons become visible.

---

### 1 · Sales — "what did we actually promise Acme?"

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

**What makes it work:** identifier-based [correlation](cases.md) rather than entity resolution ·
[fan-out](ingestion/crawlers.md) turning one recording into four artifacts · the
[intent index](retrieval/indexes.md) surfacing commitments · **`event_time`** ordering the timeline
by when things happened, not when a backfill ran

---

### 2 · Company knowledge — "why did we decide this?"

A new engineer asks why the service is structured a particular way. The answer is in a design doc,
a Slack thread that changed it, and a decision nobody wrote down except in a meeting.

**Sources:** Drive and Google Docs · Slack · GitHub PR discussions · Notion · Zoom transcripts

**Asks:** *"Why did we move off the old queue?"* · *"What was decided about retention?"* ·
*"Who owns this service?"*

**What makes it work:** the [temporal graph](architecture.md) answering *what was true when* rather
than only what is true now · **[conflict surfacing](retrieval/quality.md)** — when the design doc
and the later thread disagree, both are shown rather than one silently winning · Google Docs via
the **`export`** verb, since they have no bytes to download · citations pointing at the original
so the reader can check

---

### 3 · Meetings — "what did I commit to?"

Six calls a day, and the follow-through lives in whichever transcript nobody re-reads.

**Sources:** Zoom, Google Meet, Teams — recordings and transcripts

**What happens:** each recording fans out; the transcript routes to the communication agent with
diarization, so *who said it* survives. Decisions, action items and commitments extract as
first-class fields, not prose.

**Asks:** *"What did I agree to this week?"* · *"What's still open from the Tuesday sync?"* ·
*"Did anyone commit to a date?"*

**What makes it work:** the [intent index](retrieval/indexes.md) — decisions and commitments as
structured output rather than something to search prose for · **[memories](memories.md)** grouping
a recurring meeting series, with the `session` type compressing older instances while keeping the
extracted commitments · media transcription, which is the **largest cost exposure of any format
group** and is gated behind explicit opt-in for that reason

---

### 4 · Support — "has anyone seen this before?"

A ticket arrives describing a failure mode. Somebody solved it eighteen months ago.

**Sources:** Zendesk or Jira · internal docs · Slack engineering channels · past resolved tickets

**Asks:** *"Has this happened before?"* · *"What fixed it?"* · *"Which customers are affected by
this bug?"*

**What makes it work:** the **[question index](retrieval/indexes.md)** — the ticket says *"it just
spins forever after they hit save"* and the engineer searches *"performance regression"*, which
appears nowhere in the text · [normalized facets](ingestion/normalization.md) making *"open tickets
mentioning this component"* a filter rather than a semantic guess · similar-case retrieval over
case-level embeddings

---

### 5 · Clinical — a patient timeline

Encounters, labs, imaging reports and notes accumulate across systems and years.

**Sources:** EHR exports · lab systems · imaging reports (often scanned PDFs) · clinical notes ·
scheduling

**What happens:** records correlate onto a patient **case** by MRN — a deterministic join.
Scanned reports take the OCR path, which is a different pipeline and a different cost profile from
digital PDFs. Everything orders by **`event_time`**: when the lab was drawn, not when it was
imported.

**Asks:** *"Summarise this patient's cardiac history"* · *"What was the creatinine trend?"* ·
*"What medications were active in March 2024?"*

**What makes it work:** [cases declared, never inferred](cases.md) — **two patients with the same
name must never merge**, which is exactly what entity resolution would do · `event_time` ordering,
because a backfilled 2019 X-ray appearing after this week's lab is a timeline that renders
perfectly and misleads clinically · [break-glass](security/access-model.md) with justification and
audit · **[classification-gated inference](security/compliance.md)** pinning PHI to local models
and failing closed rather than falling through to a third party

> **Constraint worth stating.** The pipeline ships a Medical/DICOM agent, so HIPAA is in scope.
> In a hosted deployment that needs a BAA with every sub-processor touching PHI — including
> inference providers. **This is currently a self-hosted-only story** unless the cloud inference
> choice changes.

---

### 6 · Legal — a matter

Filings, correspondence, discovery documents and privileged internal analysis, over years.

**Sources:** document management · email · e-discovery exports (often multi-GB `mbox` or archives)

**Asks:** *"What's our exposure on the indemnity clause?"* · *"When did we first learn about this?"*
· *"Which documents mention the March meeting?"*

**What makes it work:** **asserted vs inferred** membership — in a legal context *"this document is
in the matter"* and *"this document appears related"* are categorically different claims, and a
system that collapses them is useless for either purpose · **[ethical walls](security/access-model.md)**
as case-level deny lists that survive membership changes · **[legal hold](operations/deletion.md)**
blocking erasure and returning partial completion naming what was withheld · streaming `mbox`
parsing, since a discovery export is one multi-gigabyte file containing fifty thousand messages

---

> **Worked versions with real records and sequence diagrams** are in
> [examples/](examples/README.md).

### The pattern across all six

Every scenario is the same shape: **heterogeneous sources, one subject, a question that spans
them.** The domain changes what the sources are and how strict the privacy is. The machinery does
not.

That is the argument for building the intersection rather than six vertical products — and it is
also why the correlation, provenance and privacy work is not optional infrastructure. Take any of
it out and the scenarios stop working in ways nobody notices until someone acts on a wrong answer.

## The five families

| Family | Covers | Needs |
|--------|--------|-------|
| **A · Personal memory** | Universal search, ask-your-life Q&A with citations, timeline reconstruction, contact recall, commitment tracking; reachable from any messaging app | Breadth of connectors, cheap local inference |
| **B · Team memory** | Shared knowledge base, onboarding, meeting intelligence, customer 360, institutional memory, cross-tool reporting | Per-item privacy, deep connectors |
| **C · Agent infrastructure** | Memory layer for other AI — MCP tools, persistent context across sessions, RAG backend | Sub-second retrieval, composable primitives |
| **D · Embedded backend** | Private memory behind someone else's product — workspaces, scoped keys, project-isolated retrieval | Tenancy contracts, quotas, purge and export |
| **E · Governance** | Data inventory, erasure and DSAR execution, audit trail | Falls out of the architecture — *if designed early* |

## The tensions, and how each resolves

| Tension | Resolution |
|---------|------------|
| **Personal and team want opposite privacy defaults.** A wants everything indexed and surfaced; B must guarantee no teammate sees your personal mail. | **New mechanism** — data inherits its ACL from the *connection* that produced it, not the space it lands in. |
| **Product vs platform RBAC.** B needs mem-dog to enforce; D delegates to the host. | **Unify** — one enforcement path. The host model becomes the case where a service identity is a single broad principal. |
| **Breadth vs depth.** A wants 300 shallow connectors; B's customer 360 needs three handled deeply. | Already solved by the tier model — Tier 3 for breadth, Tier 1 for depth. An ordering, not a contradiction. |
| **Cost profiles diverge.** A on local models is $0; D at 1k workspaces is a firehose. | Configuration — per-org budget caps plus tier policy. |
| **Latency expectations diverge.** C needs sub-second in-loop; B tolerates ten seconds. | Already solved by the eager / deferred / adaptive index split. |

### The unifying rule

**ACL inheritance follows the connection, not the container.**

A connection carries a scope — `personal` or `shared` — set at connect time. Personal Gmail
inside a team org produces `private` items regardless of project defaults; a team Slack connected
as `shared` produces member-visible items. Same org, same pipeline, opposite defaults, no
contradiction.

It also closes the proxy authorization hole: permission becomes "owns this connection, or it is
shared" rather than "authenticated to the org".

## What supporting everything rules out

1. **No single global privacy default** — visibility must be connection-scoped and space-aware.
2. **No fixed five-mode retrieval API** — Family C requires composable primitives.
3. **No host-delegated-only RBAC** — mem-dog must enforce natively.
4. **The delete cascade cannot be deferred** — governance is table stakes for B and D.
5. **The global unscoped `API_KEY` must go** — it voids every ACL the other families depend on.

## Build order

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

## Where the differentiation sits

| Capability | Position |
|---|---|
| Universal search | commodity |
| RAG backend | commodity — mem0, Zep, LlamaIndex |
| Temporal knowledge graph | contested — Zep owns the engine mem-dog runs |
| Self-hosted, air-gapped, $0 | **table stakes** — Onyx does it under MIT |
| **Messaging-channel ingestion** | **genuinely unique** |
| **Connectors × memory × private** | the wedge — the intersection is unoccupied |

See [competition/](competition/) for the full analysis.
