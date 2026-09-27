# The Negative Space

*What a memory system owes you when it doesn't find something.*

---

## The complaint with no answer

Every retrieval system eventually receives the same bug report, and it is never
a bug report. Someone types a question, reads the answer, and says:

> "It's missing something. I know that's in there."

Now go and fix it. You have a ranked list of what came back. You do not have the
one thing you need, which is what *didn't*.

That sentence has at least four causes, and they are not related to each other:

1. The record matched, ranked below the cut, and was dropped.
2. The record is in the system but was never indexed, so it could not have matched.
3. The record exists and the person asking is not allowed to see it.
4. The record genuinely isn't there — somebody was thinking of a different corpus.

Each needs a different fix. The first is a tuning problem. The second is a
pipeline problem. The third is not a problem at all — it is the system working.
The fourth is a conversation, not a change.

A ranked list cannot tell these apart, which is why the standard debugging
procedure is a person opening a database console. open-mem is largely an argument
that this information belongs in the response.

---

## What a response says

```jsonc
POST /api/v1/retrieve   {"query": "what did we promise Acme about SSO", "match": ["vector","lexical","graph"]}

{
  "results": [
    { "text": "Sofia raised data residency as a blocking concern…",
      "score": 0.030, "matched_by": ["gph", "vec"], "state": "enriched" },
    { "text": "We will deliver SAML single sign-on to Acme by 30 September 2026…",
      "score": 0.016, "matched_by": ["vec"],        "state": "enriched" }
  ],
  "excluded": [
    { "data_id": "data_01M189AVAH…", "reason": "threshold",        "score": 0.0161 },
    { "data_id": "data_01M189AVB9…", "reason": "not_yet_enriched", "state": "stored" }
  ],
  "corpus":     { "total": 42, "stored": 0, "searchable": 0, "enriched": 42 },
  "model_id":   "gemini-embedding-001@768",
  "generator_version": "gen_d325cb0586ca30eabcad5539b1dc0a7c"
}
```

Four fields there are doing work that a score cannot do.

**`excluded`** answers causes 1 and 2 directly. `threshold` means retrieval found
it and ranked it too low — raise the limit, or look at why the score is what it
is. `not_yet_enriched` means the record was eligible and *could not have
matched*, because nothing had indexed it yet. Those two words are the difference
between tuning a ranker for a week and noticing the pipeline is behind.

**`corpus`** answers cause 4 by refusing to be vague about scale. Forty-two
records were eligible, all of them enriched. If that first number is small, the
answer is thin for a reason that has nothing to do with retrieval quality. If the
`stored` count is large, a backlog is quietly eating the corpus and no amount of
prompt tuning will help.

**`matched_by`** says which arm earned each hit. The first result above never
contains the string *SSO* — it surfaced because the graph arm walked from an
entity the question named to a record asserting a relationship to it. Blended
scores hide that. When someone asks why a result is there, "the graph found it,
the vector index did not" is an answer; "0.030" is not.

**`generator_version`** is a fingerprint of prompt + model + schema + parser +
chunker. Change any one of them and everything the old one produced becomes
detectably stale — without anyone having to remember to bump a number, which is a
thing nobody remembers to do.

Cause 3 is deliberately absent from that list, and the next section is why.

---

## Three states, and one of them is a trap

Every record is `stored`, `searchable`, or `enriched`, and the states are ordered:

```
write  ──▶  stored  ··▶  searchable  ··▶  enriched
       solid          embedding        entities · edges · summary
```

The solid arrow commits before the request returns. Ingest latency is a database
write, not a model call, so the enrichment pipeline being down delays enrichment
rather than losing data.

Both dashed arrows are optional, and off by default for crawlers. That default is
correct — a crawler can discover fifty thousand records unattended, and enriching
them is a model call per chunk on data nobody has asked about yet — but its price
has to be said out loud:

> A `stored` record is durable, correct, and **invisible to search.** Both
> retrieval arms read the chunk table, and chunks are written by the embed step.

This is the single most confusing thing about the system for a new user, and it
is confusing in the worst possible way: everything looks fine. The write returned
200. The record is in the list. The count went up. It just never comes back.

Which is exactly why `state` is stamped on every citation, `not_yet_enriched`
appears in `excluded`, and `corpus` breaks the count down by state. The design
answer to a silent failure is not to eliminate it — the default is worth keeping
— but to make it impossible to hit without being told.

---

## The access rule is a predicate, not a filter

```bash
GET /api/v1/data/data_01M189AVW7FG67PERE2A30WR5J

  owner      → 200  {"data_id": "data_01M189AVW7…", …}
  colleague  → 404  {"detail": "not found"}
```

Same org, same project, same endpoint. A record you may not read is
*indistinguishable* from one that does not exist, because a 403 still discloses
that it exists — and existence is often the sensitive part. That a patient has a
file. That a candidate has a record. That a matter exists at all.

This is also why cause 3 never appears in `excluded`. Reporting that three
results were withheld is the disclosure. The response cannot mention what it is
hiding, so it doesn't: it returns the right results, not a filtered version of
the wrong ones.

That distinction — **predicate inside the query, never a filter over results** —
sounds like a style preference and is not. Asking a database for ten and hiding
three gives you seven. Asking for the ten you may see gives you ten. The first
one is wrong in a way that gets worse as permissions get more interesting, and it
leaks on the way.

The same predicate serves search, chat, and graph traversal, so there is one
place for it to be wrong instead of three.

---

## One database, and what that costs

```
PostgreSQL
  ├─ records · ACL · audit      (rows)
  ├─ embeddings                 (pgvector)
  ├─ lexical index              (tsvector)
  └─ entities · typed edges     (recursive CTEs)
```

No vector database. No search cluster. No graph database.

This costs real things and they are not oversights. It will not outscale a
dedicated store. Graph traversal is one hop — neighbourhoods, not the chain
connecting two named things. Four or more hops, variable-length path finding, or
edges past roughly 10⁸ would be the point to revisit it, and none of that is
close.

It buys two things a second store cannot.

**A path through a record you may not read is never returned at all**, because
the visibility predicate is joined into the recursive term rather than applied
after it. Post-filtering a traversal leaks structure: returning the endpoints of
a path tells you the middle exists.

**An erasure certificate can re-query every table that could hold a trace.** Not
"the cascade reported success" — the cascade is the thing under test. The
verification walks chunks, embeddings, versions, artifact sources, query sources,
memory members, case members, entity mentions, entity edges, normalized records
and share links, counts what survived, and additionally asserts that no derived
fact is still open with nothing supporting it. It is only as good as its list of
tables, and a list that stops at a database boundary stops being a guarantee.

That is the whole argument for one store, and it is a *narrow* argument: at this
size, those two properties are worth more than Cypher. Traversal sits behind a
`GraphStore` seam, so it stays a decision rather than a fact.

---

## What this is not

A post that only lists strengths is not read as confident.

- **No users.** This is a prototype. Mem0 processes more API calls in a quarter
  than this has served in its life.
- **Nothing in the connector catalog has been run against a live account.** All
  37 entries report `verified: false`, because verifying one needs somebody's
  credential. The mandatory dry run is where an entry stops being a researched
  guess.
- **No point-in-time facts.** Edges carry no validity interval, so *"who worked
  there in 2024"* is unanswerable. Zep does this natively and this does not.
- **No deterministic foreign-key edges.** `Contact.AccountId` is a certain fact,
  but the only path into the edge table is a model reading text — so an exact
  edge gets re-derived as a probabilistic one.
- **The `not_yet_enriched` list is capped at 25.** It is a diagnostic, not an
  inventory; `corpus.stored` is the number to trust for scale.
- **Self-hosting is not the differentiator**, despite being the obvious thing to
  claim. Onyx is MIT-licensed and SOC 2 Type II with 40+ connectors; Khoj runs
  entirely on local models. Private deployment is table stakes here.

---

## The point

The interesting problems in AI memory are almost never the AI. They are that a
record can be present and unindexed. That a summary written last month still
contains the paragraph someone asked you to delete. That a 403 is a disclosure.
That a nightly crawl re-embeds everything unless someone thought about etags.
That ten thousand rows written in a second become ten thousand model calls over
the next four hours.

None of that is interesting the way a new model architecture is interesting. All
of it is what stands between a demo that impresses a room and a system somebody
still trusts a year later — after connections expired, models changed, prompts
got tuned, documents got revised, someone left the company, and someone else
asked to be forgotten.

The measure of a memory system is not how good the answer is when it works. It is
whether you can tell what happened when it doesn't.

---

## Read next

| | |
|---|---|
| [Building open-mem: Notes on What AI Memory Actually Requires](../presentation/blog.md) | The long read — ingestion pipelines, silent failures, vector spaces that don't line up, and what we'd tell someone starting over |
| [`docs/usage.md`](../usage.md) | Six scenarios against a running system — write and ask, pull from an app, receive a webhook, build the graph, backfill a cold crawl, erase with proof |
| [`docs/graph.md`](../graph.md) | Why the graph is not a graph database, what it costs, and what was true when |
| [`docs/operations/deletion.md`](../operations/deletion.md) | Cleanup versus erasure, the cascade, legal hold, and verifying rather than claiming |
| [`docs/design-principles.md`](../design-principles.md) | The central bet, the cross-cutting invariants, and who owns which capability |
| [`TBD.md`](../../TBD.md) | Twelve decisions designed but not decided, ordered by how expensive each becomes if made late |
