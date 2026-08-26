# Index Construction

The pipeline's purpose is not summarization — it is **building retrieval structures**. Classic
inverted indexes map terms that *appear* to documents. An LLM lets you index over vocabulary the
document never contains:

> A ticket saying *"it just spins forever after they hit save"* should be findable by
> *performance regression*, *data loss risk* and *escalation candidate* — none of which are in
> the text.

## The index set

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

## Not every item deserves every index

Six index types per item means up to 6× the LLM calls. A log line does not need a question index;
a contract does.

The control surface already exists — the per-agent processing flags (`extract_entities`,
`extract_actions`, `extract_topics`, `embed`) **are** index-selection flags.

## Build cheap eagerly, expensive lazily

| Tier | What | When |
|------|------|------|
| **eager** | chunks, vectors, FTS, structure, facets | always, on ingest |
| **deferred** | questions, claims, concepts, summaries | on demand |
| **adaptive** | expensive indexes for items that actually get retrieved | after N retrievals |

Most corpora have a long cold tail nobody ever queries. The adaptive tier concentrates spend on
content that demonstrably matters, at the cost of a slower first query on cold content.

## Every index is a privacy leak surface

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

## Composable retrieval

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
