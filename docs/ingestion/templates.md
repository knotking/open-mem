# Templates — telling it what the thing is *for*

**Not built.** This is a design.

Extraction is routed today by `data_type`, which is derived from the bytes: a
`.docx` is a `document`, a `.wav` is `audio`. That is the right default and it
has a ceiling built into it —

> **The bytes cannot tell you what the document is for.**

A novel, a design document, a contract and a lab report are the same
`data_type`, and they are interesting for entirely different reasons. Asking one
prompt to serve all four produces the same bland envelope for each: a title, a
paragraph of summary, some keywords. Correct, and almost useless for the design
document, where the thing worth keeping is *what was decided and what was
rejected*.

A template is the missing axis: **declared intent**, supplied by the caller,
orthogonal to the sniffed type.

```
data_type   what it IS      derived from bytes    docx → document
template    what it is FOR  declared by caller    design-doc, book, contract
```

---

## How it composes with what exists

Nothing here replaces the current mechanism; it adds a term to it.

```
skeleton  (extraction.py — injection defence, never per-template)
   + type block     (prompts.py — what is interesting about a document)
   + template block (new — what is interesting about a design document)
```

**The skeleton stays out of reach.** Per-agent variation of it was already
rejected as "a per-agent hole"; a per-template variation is the same hole with a
friendlier name. A template contributes the *interesting-ness* paragraph and
nothing else.

**Versioning falls out for free.** A prompt is hashed into `generator_version`,
so a template is too: editing one makes every artifact it produced detectably
stale, and `/reprocess` already exists to rebuild them. This is why templates
should be data in a registry rather than strings passed in by callers — an
inline prompt cannot be versioned, which is exactly why `enrichment.prompt` is
deliberately never persisted.

**The envelope should not change.** Different templates want different fields —
a contract wants obligations, a design document wants rejected alternatives.
Letting each declare its own schema breaks the property that every record is
comparable to every other. Extras belong in `structure`, which is already a free
-form dict; the core envelope stays fixed.

---

## The catalogue

Grouped by the question each is trying to answer. The *point* of a template is
the "look for" line — the rest is scaffolding.

### Documents

| Template | Look for | Trap it avoids |
|---|---|---|
| `book` | thesis, argument structure, recurring concepts, who and what recurs across chapters | summarising plot beat by beat; a chapter list is not a summary |
| `design-doc` | the problem, the chosen approach, **alternatives considered and why they were rejected**, open questions, decision status | recording the proposal and losing the reasoning, which is the only part that ages well |
| `research-paper` | question, method, dataset, findings, **stated limitations** | limitations are buried in the discussion and are the first thing a reader needs |
| `contract` | parties, obligations per party, dates, termination, liability, governing law | inference — an obligation must be quoted, never paraphrased into existence |
| `policy` | scope, who it binds, obligations, exceptions, effective date | flattening "should" and "must" into the same claim |
| `runbook` | steps in order, preconditions, failure modes, rollback | losing ordering, which is the entire value |
| `meeting-notes` | decisions, owners, deadlines, **disagreements** | recording consensus and dropping the objection |
| `report` | period, metrics, movement against prior period, anomalies | numbers without their period are unusable |
| `invoice` | parties, line items, amounts, dates, currency | currency and tax treated as decoration |
| `spec` | endpoints, parameters, error cases, versioning | happy path only |
| `correspondence` | commitments made, requests, deadlines | pleasantries |
| `cv` | roles with dates, skills, employers | **sensitive by default** — see below |

### Media

| Template | Look for |
|---|---|
| `lecture` | topic, argument, worked examples, questions asked |
| `meeting-recording` | decisions, owners, deadlines, who dissented |
| `interview` | claims and who made them; keep attribution per speaker |
| `podcast` | topics with positions, guests, where each segment starts |
| `screencast` | **what is on screen** — the visual track is the content, not decoration |
| `voice-note` | intent and any commitment; these are short and mostly action |

### Images

| Template | Look for |
|---|---|
| `screenshot` | UI state and **error text verbatim** — OCR is primary, description secondary |
| `whiteboard` | boxes, arrows and their direction; handwriting via OCR |
| `diagram` | components and the **relationships between them** — this feeds the graph |
| `chart` | axes, units, series, the trend the chart shows |
| `photo-of-document` | OCR primary; the photo itself is not the content |
| `receipt` | merchant, total, date, line items |

### Tabular

| Template | Look for |
|---|---|
| `dataset` | schema, row count, column meanings, a sample — never the whole table |
| `ledger` | accounts, totals, period, currency |
| `tracker` | status column, owners, what is overdue |

---

## Three rules every template must carry

These are not decoration; they are what stops a template making things worse.

**An empty extraction is a correct extraction.** Every shipped prompt already
says this and every template must repeat it. A template that says "extract the
obligations" is an instruction to *find* obligations, and a model asked to find
something will find it. `contract` applied to a birthday card must return
nothing.

**Quote, do not infer.** For `contract`, `policy` and `report`, the extracted
claim must be traceable to text on the page. This is the same discipline that
makes citations checkable.

**A template is a hint, not a promise.** The caller declares intent and can be
wrong — `design-doc` on a novel should degrade to a plain document extraction,
not force decisions out of prose that contains none.

---

## Interface

**Per write**, alongside the existing options:

```json
{
  "items": [{ "external_id": "rfc-014", "content": {"kind": "inline", "text": "…"},
              "template": "design-doc" }],
  "options": { "enrich": true }
}
```

Per *item*, not per request — one write carries many items and a batch is not
uniform, which is the same reason `access` is per item.

**Saved defaults** reuse `agent_configs`: a project that only ingests contracts
sets `contract` as its default for `document` and stops declaring it per write.
Precedence follows the existing rule — project beats org beats shipped, unless
the org locked it.

**Discovery** through `GET /api/v1/templates`, the way `prompts.registry()`
already serves the data-type mapping, so no client hardcodes a list that will
drift.

**Selection in the console** belongs in step 1 of Add data — you know what you
are uploading at the moment you choose the file, not three panels later.

---

## Open questions

**Should an unknown template be refused or ignored?** Refusing is honest and
breaks a write for a typo; ignoring silently produces a generic extraction the
caller believes is specialised. Leaning refuse — a 400 naming the valid
templates, since the list is discoverable.

**Should templates be inferred?** A first page can usually be classified
cheaply. Attractive, and it must never be silent: if inferred, the row records
that it was inferred and which template, or nobody can tell a declared `contract`
from a guessed one.

**Sensitive templates.** `cv`, and any medical or legal template, name a person
by construction. These should interact with the sensitivity policy that already
gates the expensive tier — a template is a reasonable signal that a record needs
a narrower default ACL, and that is worth deciding before shipping one.

**Does a template change chunking?** A runbook chunked mid-procedure loses the
ordering it exists for. Probably yes, eventually; not in a first slice.

---

## A first slice

1. `TEMPLATES` registry in `prompts.py` — data, hashed like the rest.
2. `template` on the write item; composed after the type block.
3. `GET /api/v1/templates`.
4. Console: a template select in step 1 of Add data.
5. Six templates, not thirty: `design-doc`, `meeting-notes`, `contract`,
   `research-paper`, `screenshot`, `diagram`.

Six is enough to learn whether the axis pays for itself. The catalogue above is
a menu to choose from, not a backlog to complete — a template nobody selects is
a prompt nobody maintains.
