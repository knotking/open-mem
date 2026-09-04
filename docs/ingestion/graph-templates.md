# Premeditated templates — deciding the graph before you read

**Not built.** This is a design. It extends
[`templates.md`](templates.md) from *what to extract* to *what to connect*, and
it is the more consequential half.

---

## The problem: open-domain extraction produces a graph you cannot query

Graph extraction today is open-domain. Seven entity types
(`entities.py:37`), twelve predicates (`graph.py:41`), one prompt for every
document ever written. The instruction is, in effect, *find the entities and
relationships*.

That is a reasonable default and it has a specific failure, visible the moment
you point it at something that is not a workplace document. Seven of the twelve
predicates are org-chart shaped — `works_for`, `reports_to`, `member_of`,
`collaborates_with`, `owns`, `produces`, `uses`. Run them over the Bhagavad
Gita and almost nothing fits, so almost every edge becomes `related_to`:

> `renunciation —related_to→ action`

which asserts that two words occurred near each other. The graph is full and
says nothing. **A universal vocabulary is shaped like nothing in particular.**

---

## The inversion

A premeditated template declares the shape of the graph **before the document
is read**:

```
open-domain     find whatever relationships exist        → related_to soup
premeditated    these six edges may exist; find them     → slot-filling
```

Premeditation is the load-bearing word. The judgment about what matters is made
**once, by a person, for a class of documents** — not per document, not by the
model at read time. It is amortised judgment, and that has consequences the
rest of this document is about.

Slot-filling is also simply an easier task. *Find all the relationships* is
open-ended; *for this text, who speaks, what do they teach, and what leads to
what* is a form. Models are markedly better at the second, and so are people
reviewing the output.

---

## Five things premeditation buys

### 1. Absence becomes evidence

This is the one that matters most, and it is not obvious.

Under open extraction a missing edge means nothing. You cannot distinguish
*the model did not find it* from *it is not in the document* from *the model
was never looking for it*. Every gap is unreadable, so the graph can only be
browsed, never trusted.

When the schema is declared, **you know what was sought**. A `contract`
template that emits no `terminates_on` is now telling you something: either the
contract has no termination clause or the extraction failed, and those are two
findings rather than one silence. An empty slot is a result.

It also makes the graph measurable. Coverage per template per predicate is a
number you can watch move; "how good is our graph" currently has no denominator.

### 2. Edges become typecheckable

Every predicate carries a domain and a range:

```
teaches        person   -> topic
said_by        claim    -> person
leads_to       topic    -> topic
part_of        work     -> work
```

`Kurukshetra teaches dharma` is now rejected at write time rather than
discovered a quarter later by someone reading a bad answer. `related_to`
accepts every pair of anything, which is precisely why it accumulates garbage —
an untyped edge is an unfalsifiable one.

### 3. Confidence stops being one number for everything

A `part_of` read off a table of contents is near-certain. A `leads_to` inferred
from the rhetoric of an argument is a reading. If both arrive at `0.9` the
field is decoration.

A template is the right place to declare which of its predicates are
**structural** (stated plainly, cheap to verify) and which are
**interpretive** (a defensible reading, and someone may defensibly disagree).
The distinction is knowable in advance, per class of document, which is exactly
what premeditation means.

### 4. Derivation rules become safe to state

Transitivity is a property of a predicate in a context, not of a predicate.
`part_of` is genuinely transitive: a verse in a chapter of a book is in the
book. `leads_to` is not — anger leading to delusion and delusion to ruin does
not license *anger leads to ruin*, because each step carries its own
conditions, and collapsing them is how a graph starts producing confident
nonsense.

A template declares which of its predicates admit closure. Derived edges are
marked derived, always, so an inference can never masquerade as an assertion.

### 5. A template is versioned, so being wrong is recoverable

`templates.md` already establishes that a prompt is hashed into
`generator_version`. Extend that to the schema and the whole thing becomes
reversible: **every edge records the template that produced it.**

- editing a template makes exactly its edges detectably stale, and `/reprocess`
  rebuilds them
- two templates can be run over the same text and their graphs diffed — which
  is the only honest way to find out whether a template is any good
- a template applied in error is retractable, because you know precisely which
  edges came from it

This is what makes premeditation safe rather than reckless. You are allowed to
be wrong about the shape, because the shape has a version.

---

## The rule that keeps this one graph instead of many

**Predicates are global. Templates select from the registry; they do not invent
private vocabularies.**

If each template mints its own edge names, the result is N disconnected
subgraphs and no path from a design document to the meeting that discussed it.
The value of a graph is entirely in traversal across sources, so a per-template
namespace destroys the reason for building one.

`leads_to` serves the Gita, the incident report and the research paper. A
template's job is **selection and constraint** — which predicates apply, with
what types, what cardinality, what confidence class — plus the right to
*propose* an addition to the registry, reviewed once, available to all.

The prize is the cross-document path:

```
meeting-notes  decided   ──▶  design-doc  proposed  ──▶  incident  caused_by
```

*Which decision led to the outage* is a traversal over three templates and
three document types, and it is only expressible because all three drew from
one vocabulary.

---

## How to design one: start from the questions

Not from a list of entity types. Write down the three to five questions
somebody will actually ask of this class of document, then derive the smallest
schema that answers them.

| Template | The question it exists for | What that forces into the schema |
|---|---|---|
| `scripture` | what does this speaker say leads to what? | `said_by`, `leads_to` |
| `design-doc` | what did we reject, and why? | `considered`, `rejected_because` |
| `contract` | what does each party owe, and by when? | `obliges` (dated edge) |
| `incident` | what caused what, and what stopped it? | `caused_by`, `mitigated_by` |
| `research-paper` | what was claimed, and what limits it? | `claims`, `limited_by` |

**A predicate that serves no listed question does not go in.** That rule is the
only thing standing between this and a two-hundred-predicate ontology nobody
fills — the failure mode of every schema-first knowledge project, which
produced expressiveness in inverse proportion to use.

Hard ceiling: **six predicates, one page.** If a template needs more it is two
templates. Six is about what a model holds while reading and about what a
person will maintain.

---

## Worked example: `scripture`, on the Bhagavad Gita

```yaml
name: scripture
extends: book
questions:
  - who says what, to whom, and inside whose narration?
  - what does a speaker teach, and where?
  - what does the text claim leads to what?
  - what does it set against what?

predicates:
  said_by         claim   -> person   many   structural
  teaches         person  -> topic    many   interpretive
  leads_to        topic   -> topic    many   interpretive   transitive: no
  contrasts_with  topic  <-> topic    many   interpretive
  part_of         work    -> work     one    structural     transitive: yes
  about           section -> topic    many   structural

citation_unit: chapter:verse
aliases: on

traps:
  - Epithets are one person. Keshava, Govinda, Hrishikesha, Madhusudana and
    Janardana are Krishna; Partha, Kaunteya, Dhananjaya and Gudakesha are
    Arjuna. Resolve the node — but keep the epithet used **on the edge**, because
    it is chosen, not incidental: Hrishikesha, "lord of the senses", appears
    where the subject is sense-control.
  - The narrator is not the speaker. Sanjaya reports to Dhritarashtra what
    Krishna said to Arjuna: three frames, and flattening them attributes the
    teaching to the wrong mouth.
  - A chain is ordered. BG 2.62-63 runs dwelling → attachment → desire → anger →
    delusion → lost memory → lost discrimination → ruin. Emitted as a set of
    eight related topics it asserts the opposite of what the verse says.
  - An empty graph is a correct graph. This template on a novel yields a plain
    document graph, not manufactured teachings.
```

Why this text is the right test case: it is 700 verses of argument and no org
chart, so every weakness of the universal vocabulary shows at once — and the
fixes it forces (`leads_to`, attribution, alias sets, verse-level citation) are
the same fixes a design document and an incident report need. Nothing here is
scripture-specific except the epithets.

Two further notes it makes concrete:

**Co-mention carries no signal in a single long work.** The co-mention arm
exists so the graph is non-empty before any model has extracted a relationship
(`graph.py:20-26`), and it is well judged for a corpus of many small records
where co-occurrence is selective. Krishna and Arjuna co-occur in nearly every
chunk of the Gita; the result is a near-complete graph where every node has the
same degree. Premeditated edges are what make one long document tractable.

**The citation unit is part of the schema.** A chunk id is the wrong provenance
for scripture, where the entire commentary tradition addresses `BG 2.47`.
`timestamp` for a recording, `section` for a contract, `chapter:verse` here.

---

## The danger, stated plainly

`templates.md` warns that a model asked to find something will find it. **For a
graph this is worse by an order of magnitude**, and the reason is traversal.

A hallucinated sentence sits in a summary where a reader can discount it. A
hallucinated *edge* becomes a path. Three hops later an answer rests on it and
carries no trace of the weak link — the confidence that should have decayed
along the way is simply gone, and the output is a clean sentence with a
laundered premise.

So every template carries these, and a template that omits them is not
reviewable:

- **An empty graph is a correct graph.** `contract` over a birthday card emits
  nothing.
- **Every interpretive edge carries the span it came from.** The graph already
  stores which record asserted an edge; premeditated extraction must also store
  *which words*, because "quote, do not infer" needs somewhere to put the quote.
- **A template is a hint, not a promise.** Declared intent can be wrong, and the
  wrong template must degrade rather than fabricate.
- **Confidence decays along a path**, and a path crossing an interpretive edge
  is reported as such at the point of answering.

---

## Where premeditation comes from

Three sources, in descending order of trust — the same ladder `templates.md`
sets out for extraction:

1. **Declared per item by the caller.** Cheapest and most reliable; the caller
   knows what they are uploading at the moment they choose the file.
2. **A saved project default.** A project that only ingests contracts should
   stop declaring it every time. Precedence follows the existing rule: project
   beats org beats shipped, unless the org locked it.
3. **Inferred from the first page.** Attractive and must never be silent — an
   inferred template is recorded as inferred, or nobody can tell a declared
   `contract` from a guessed one, and the confidence attached to its edges is
   built on a guess.

**One document may carry more than one graph.** A design document is also
correspondence; applying both templates is not a conflict, because every edge
records the template that drew it. Templates are lenses, and a record can be
read through several. This falls straight out of storing the template on the
edge, and it is the property that makes the whole scheme additive rather than
exclusive.

---

## A first slice

1. `said_by`, `teaches`, `leads_to`, `contrasts_with` added to the global
   predicate registry, with domain and range — useful immediately, before any
   template exists.
2. Alias sets on entities, so name variants resolve to one node while the
   variant stays on the edge.
3. `template` recorded on every edge, and hashed into `generator_version`.
4. `confidence_class` per predicate — structural or interpretive — surfaced
   wherever an answer traverses.
5. Three templates: `scripture`, `design-doc`, `incident`. Deliberately unlike
   one another, because three near-identical templates teach nothing about
   whether the axis pays for itself.

Steps 1 and 2 are additive to the existing extraction and worth doing whether or
not the rest is built.
