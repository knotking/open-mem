# The Graph

Entities and the relationships between them, in the record store.

Two questions this answers that flat retrieval cannot: **"what else is connected
to this?"** and **"how do these two relate?"** Everything below is built and
running; the gaps are named at the end rather than implied away.

## Why it is not a graph database

The traversal and the access rule have to be the same query.

Filtering a traversal afterwards leaks structure. If a path runs through a
record you cannot read, returning its endpoints tells you that record exists —
which is precisely what its ACL forbids. So the visibility predicate is joined
into the recursive term, and a path touching anything invisible is never
returned at all.

A second store would also be a second place personal data hides from
[`verify_erasure`](operations/deletion.md), which already re-queries every table
that could hold a trace. That check is only as good as its list of tables, and a
list that stops at the database boundary stops being a guarantee.

Both properties are worth more than Cypher at this size. Neither is permanent:
traversal sits behind a `GraphStore` seam, so the decision stays a decision.

**When to revisit.** Four or more hops, variable-length path finding, or edges
past roughly 10⁸. None of that is close.

## Two kinds of connection

They are deliberately not merged, because they carry different weight.

| | Asserted edge | Co-mention |
|---|---|---|
| **What it is** | A claim a document made | Two entities named in the same record |
| **Where it lives** | `entity_edges` | Computed from `entity_mentions` |
| **Needs a model** | Yes — extraction must read for relationships | **No** |
| **Evidence** | The record that said so, per edge | The count of shared records |
| **Strength** | A specific claim, and it can be wrong | Weak. Appearing together is not a relationship |

The second row is the one that matters in practice. **Co-mentions work the
moment entities exist**, before any model has read a document for relationships
— which is the common case early on, and the case you are in whenever the
extractor is degraded or rate limited.

Co-mentions are not stored. `entity_mentions` already records them, and a
materialised copy would go stale.

## The predicate vocabulary is closed

```
works_for   member_of   reports_to   collaborates_with
located_in  part_of     owns         produces
uses        attended    about        related_to
```

An open vocabulary degrades into unqueryable free text, and the entire value of
a typed edge is being able to ask for all of them. A relation with a predicate
outside this list is dropped rather than stored.

`related_to` exists as the honest escape hatch. The extraction prompt says so
explicitly: *a precise-looking wrong edge is worse than a vague right one,
because nothing downstream can tell it was a stretch.*

Serve the list rather than copying it into your client:

```http
GET /api/v1/graph/predicates
```
```json
{ "predicates": ["works_for", "member_of", "…"], "max_depth": 3 }
```

## How an edge gets made

Relations ride the extraction pass that already reads the text, so this costs no
extra model call. The model names endpoints **by name**, because it cannot know
our identifiers:

```json
{
  "entities": [
    { "name": "Priya Raman", "type": "person", "identifier": "priya@northwind.example" },
    { "name": "Northwind Trading", "type": "organization" }
  ],
  "relations": [
    { "subject": "Priya Raman", "predicate": "works_for", "object": "Northwind Trading" }
  ]
}
```

Resolution runs first, then edges are matched back against what it produced for
that same record. **A relation naming something the resolver did not produce is
dropped, never guessed at** — inventing an endpoint would attach a real claim to
the wrong node, and the graph has no way to show that later.

Both happen inside the enrichment transaction, so an item is never enriched with
entities but no edges, or the reverse.

## Reading the graph

### The neighbourhood around an entity

```http
GET /api/v1/entities/ent_01JQRS.../graph?depth=2&predicates=works_for,located_in
```

```json
{
  "root":  { "entity_id": "ent_01JQRS…", "display_name": "Priya Raman",
             "type": "person", "depth": 0 },
  "nodes": [
    { "entity_id": "ent_01JQXY…", "display_name": "Northwind Trading",
      "type": "organization", "depth": 1 },
    { "entity_id": "ent_01JQZZ…", "display_name": "Lisbon",
      "type": "location", "depth": 2 }
  ],
  "edges": [
    { "subject_id": "ent_01JQRS…", "predicate": "works_for",
      "object_id": "ent_01JQXY…", "evidence": 3,
      "source_data_ids": ["data_01…", "data_02…", "data_03…"], "confidence": 0.8 }
  ],
  "truncated": false
}
```

`depth` is 1–3. `predicates` is optional and narrows the traversal itself, not
the result.

**`evidence` is a count of distinct records, and it is the field to read first.**
One document asserting something is a claim; three asserting it independently is
closer to a fact. Collapsing that into a boolean throws the distinction away.

**Edges traverse in both directions.** Which end was written as the subject is a
grammatical accident of the sentence, not a fact about the relationship — so
asking Northwind for its neighbours finds Priya.

### Who appears alongside

```http
GET /api/v1/entities/ent_01JQRS.../co-mentions?limit=25
```

```json
{
  "co_mentions": [
    { "entity_id": "ent_01JQXY…", "display_name": "Northwind Trading",
      "type": "organization", "shared_records": 7 },
    { "entity_id": "ent_01JQAB…", "display_name": "Lisbon",
      "type": "location", "shared_records": 2 }
  ]
}
```

`shared_records` is the whole signal. Seven shared records is worth attention;
one is worth almost nothing, and the count is reported rather than thresholded
so the reader decides.

## What visibility means here

An entity is visible only through its mentions, and a mention inherits the ACL
of the record it came from.

- **An entity nobody can see a mention of does not appear at all.** Listing it
  would disclose that a record exists.
- **A traversal stops at a record the caller cannot read.** Visibility is
  enforced on every hop, not on the result — arriving at a node via a hidden
  record would disclose the hidden record.
- **Requesting an invisible entity returns `404`, not `403`.** Confirming it
  exists is itself the disclosure.
- **Counts report what the caller can see**, never the true total. "42 mentions"
  shown against a list of three is a disclosure in a number.

## Erasure

An edge names the record that asserted it. Deleting the record deletes the
claims it made — otherwise the graph asserts something with no evidence behind
it, and the endpoints of that edge are themselves derived personal data.

`entity_mentions` and `entity_edges` are both deleted explicitly in the purge and
both checked by `verify_erasure`, rather than left to a foreign key cascade
nobody re-checks.

## Worked example

Three records arrive:

```
1. "Priya Raman (priya@northwind.example) led the incident review at Northwind Trading."
2. "P. Raman confirmed the monitoring work lands before the Lisbon summit."
3. "Northwind Trading is headquartered in Lisbon."
```

**Resolution** produces three entities. Records 1 and 2 collapse onto one person
despite different names, because they share an identifier — the only evidence
strong enough to join across names. Record 3 adds nothing new.

**Edges** from records 1 and 3:

```
Priya Raman  --works_for-->      Northwind Trading   (evidence: 1)
Northwind    --located_in-->     Lisbon              (evidence: 1)
```

**Traversal** from Priya at depth 1 returns Northwind. At depth 2 it also
returns Lisbon — two hops away, through a relationship Priya was never named in.

**Co-mentions** for Priya return Northwind and Lisbon with `shared_records` of 2
and 1, and would have returned them even if no relation had been extracted at
all.

**Deleting record 3** removes the `located_in` edge. Lisbon survives as an
entity — record 2 still mentions it — but nothing asserts where Northwind is any
more, which is correct: the only thing that said so is gone.

## In the console

**Organize → Entities.** Selecting an entity shows its connections at 1, 2 or 3
hops, each edge with the records that assert it, and the co-mentions beneath.
When no relationships have been extracted the panel says so and points at the
co-mentions, rather than showing an empty graph and implying there is nothing
there.

## As a retrieval arm

`match: ["vector", "lexical", "graph"]` adds a third arm, fused by the same
reciprocal rank fusion as the other two and reported in `matched_by` as `gph`.

What it contributes is the thing neither other arm can: a record that does not
contain the words searched for and is reachable only across a relationship some
other document asserted. A search for *"Priya Raman"* returns the quarterly
revenue note, because a different record said Priya works for Northwind and the
note mentions Northwind.

Four properties make it defensible rather than magic:

**The access rule is inside the traversal.** An edge is only traversable when
the record that asserts it is readable. Walking first and filtering after would
still surface the far endpoint — and the existence of a connection is itself
what the unreadable record's ACL protects.

**An entity is a seed only through a record the caller can read.** Resolving
against the entity table alone would confirm that a name exists in this project
to somebody who can see no record containing it.

**The seeds are reported.** `graph_seeds` names the entities the query
resolved to and how. A graph-only result contains none of the query's words, so
without the seed a reader cannot tell whether the connection was the one they
meant. An empty list says the arm found nothing to *start* from, which is a
different answer from finding nothing connected.

**It is an axis, not a default.** It answers a different question from the one
the other arms answer, and it is only as good as the entity layer beneath it —
with extraction degraded to the local heuristic there are no entities, so there
is nothing to seed and the arm correctly returns nothing.

**It resolves a full name or nothing.** "Acme" does not seed "Acme
Corporation". The entity layer resolves a mention by identifier or by exact
name and refuses to guess, because a wrong join merges two people permanently
and silently — so an arm that matched loosely would be doing the guessing the
layer beneath it declines to do, and doing it invisibly, since the seed is
reported but the near-miss that produced it would not be. The cost is real: a
query saying "Acme" gets no expansion, and the seed line is what tells you so.

One hop, and the arm returns each connected record's opening chunk: it is
claiming the *record* is connected and has no view about which passage answers
the question. Choosing a passage by relevance would be the other arms' job done
worse.

## What is not built

- **No temporal validity.** `valid_from` / `valid_to` are not modelled, so "who
  worked there in 2024" is unanswerable. This is the bitemporal layer, and it is
  worth building only if someone actually asks point-in-time questions.
- **No path finding between two named entities.** Only neighbourhoods.
- **Edge quality is bounded by extraction quality.** With the extractor degraded
  to the local heuristic there are no entities and therefore no edges — a graph
  built on a weak extractor is a graph nobody should trust.
