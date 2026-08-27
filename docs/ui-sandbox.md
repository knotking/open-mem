# The Sandbox

Upload a dataset, watch it get enriched, chat against it, and see exactly what the chat retrieved.

The sandbox is where someone decides whether mem-dog works for *their* data. Nothing else in the
product answers that question — a connector list does not, and a benchmark on someone else's corpus
certainly does not.

---

## It is a real project with a TTL, not a mode

The single most important decision, and it is a negative one:

> **There is no sandbox code path.** A sandbox is a project with `sandbox: true` and a TTL. It runs
> the identical write path, the identical workers, the identical retrieval.

A special "demo mode" with its own shortcuts tests the demo mode. Whatever the user concludes from
it is not transferable, and worse, it is *convincingly* not transferable — it looks like evidence.
The sandbox is only worth building if what you see in it is what production does.

The cleanup machinery already exists and needs nothing new:

| Need | Mechanism that already covers it |
|------|----------------------------------|
| Expires automatically | A memory type with a TTL and `orphan_delete` |
| Removes derived artifacts | The [deletion cascade](operations/deletion.md) |
| Scoped away from real data | [Project isolation](security/tenancy.md) — every query is already scoped |
| Bounded cost | Admission control and token budget, per project |

A sandbox is therefore a *configuration* of things that exist. If building one requires new
deletion, new scoping or new limits, that is a signal those mechanisms were not general enough.

---

## "Sandbox" is a word that makes people paste production data

This needs saying before the feature is designed, because the naming does real damage.

People treat a sandbox as consequence-free. They will upload real customer exports, real clinical
notes, real contracts — precisely because it is "just a test". The data is real; the TTL does not
change that; a 7-day retention of PHI is still PHI.

**So the sandbox is not a lower-security zone, and must not behave like one:**

- It inherits the project's ACL defaults, privacy policy and model-routing allow-list. A project
  whose chain is BAA-restricted stays restricted in its sandbox.
- Its contents are auditable and erasable like anything else.
- **The upload surface says what it is** — that this is real ingestion into real storage under real
  retention, expiring on a date it names.

The failure to avoid is a sandbox that quietly routes to a cheaper, unrestricted model because "it
is only a test". That converts a convenience into a disclosure, and it is the
[same fallback-chain boundary](use-cases-catalog.md) in a friendlier costume.

---

## The upload step

Reuses [bulk operations](operations/bulk-operations.md) — same run entity, same dry-run, same
per-item results. Nothing bespoke.

| Input | Path |
|-------|------|
| CSV / JSONL | Bulk write, one item per row |
| A folder of documents | Batch upload → `Stored` refs |
| A live connector | Normal connector sync, scoped to the sandbox project |
| Paste | A single inline item, for a quick look |

### Sample first, by default

Enriching 100k uploaded rows before the user has looked at one is the expensive mistake this
feature invites. The default is to **enrich a sample, show it, then ask**.

```
1. Ingest everything          cheap — stored and searchable
2. Enrich a sample (~50)      the user looks at real output on their own data
3. Enrich the rest            only on an explicit, costed decision
```

Step 2 is where the actual product judgement happens, and it costs almost nothing. Step 3 is where
the money is, and it should never be implicit. The cost estimate shown at step 3 comes from the
[token accounting](operations/token-accounting.md) already built for budgets.

---

## The readiness staircase is the whole UI problem

[The write API](ingestion/write-api.md#readiness-is-a-staircase-and-clients-need-to-see-it) defines
three states — `stored`, `searchable`, `enriched`. In the sandbox they stop being an API detail and
become the interface.

Someone uploads 500 records and immediately asks a question. Enrichment has not finished. The
answer is thin. **They conclude the product does not work** — and they are wrong for a reason the
UI could have shown them.

```
  uploaded  ████████████████████████  500
  stored    ████████████████████████  500
  searchable████████████████░░░░░░░░  341
  enriched  ██████░░░░░░░░░░░░░░░░░░  126   ~4 min remaining
```

Two consequences for the chat surface:

- **It says what it is answering over.** *"Answering over 126 enriched of 500 records"* — one line,
  and the early answer becomes informative rather than damning.
- **It offers to wait.** Not a spinner blocking the UI; an explicit "ask again when enrichment
  finishes" that re-runs the same question and shows the difference.

That second one is quietly the best demo in the product: the same question, answered before and
after enrichment, side by side. It demonstrates what enrichment *is* better than any description.

---

## The output is the retrieval trace, not the answer

**This is the finding that decides whether the sandbox is a toy or a tool.**

A chat sandbox that shows only the answer is a demo. The answer is a *lagging indicator* of
ingestion quality, filtered through a model that is good at sounding right regardless. If the
answer is bad, it tells you nothing about why: bad chunking, wrong embedding model, the record
never got enriched, retrieval found the wrong thing, or the model fumbled a correct context.

So the sandbox shows the trace, and the answer is secondary:

| Panel | What it answers |
|-------|-----------------|
| **Retrieved chunks, ranked, with scores** | Did retrieval find the right records? |
| **Source record per chunk**, openable | Is the chunk boundary sane, or did it split a claim from its subject? |
| **What was actually sent to the model** | Is the failure retrieval or generation? |
| **`model_id` and `generator_version`** | Which configuration produced this, so it is reproducible |
| **Records considered but filtered** | Was it excluded by ACL, by score, or by not being enriched yet? |

The last row matters more than it looks. *"The answer is missing something I know is in the data"*
is the most common sandbox complaint, and it has four completely different causes with four
different fixes. Showing which one applies turns an unfalsifiable impression into a diagnosis.

> Everything here is data the retrieval path already has. The sandbox does not compute it — it
> declines to throw it away.

---

## Comparing datasets — and comparing configurations

"Upload different datasets" has two readings, and the second is the more valuable one.

**Different datasets, same configuration** — does this work for my CRM export as well as my
tickets? Two sandbox projects, results side by side. Straightforward.

**Same dataset, different configurations** — this is where the sandbox earns its cost. Run one
corpus under two chunkers, two embedding models, or two extraction prompts, and diff the retrieval
traces for the same question.

```
                    config A              config B
  chunker           semantic-1024         semantic-512
  embedding         local-mini            cloud-large
  ─────────────────────────────────────────────────────
  top-1 correct     6 / 10                8 / 10
  cost / 1k         $0.00                 $0.42
```

This is the [staleness-impact preview](operations/model-catalog.md) machinery pointed at a
question people actually ask: *is the expensive model worth it on my data?* The honest answer
varies by corpus, and this is the only way to find it.

It also produces the evidence for a decision the system otherwise forces blind: which model to
assign per purpose. **A model choice made without measuring it on the target corpus is a guess with
a monthly invoice attached.**

### The comparison has a hard prerequisite

A/B comparison is only meaningful if the two runs are genuinely comparable, which requires
`generator_version` and `model_id` recorded per artifact — [already required](ingestion/workers.md),
and the reason it is required. Comparing two runs whose configuration you cannot pin is comparing
noise.

---

## Where this sits in the plan

The sandbox is not a late polish item. **It is Phase 1's acceptance test with a face on it.**

Phase 1's goal is *"write an item, find it by search, get it back."* The sandbox is that loop, made
visible, on the user's own data — which means building it exercises the spine end to end and
produces the first genuinely demonstrable thing.

| Slice | Sandbox capability |
|-------|--------------------|
| **Phase 1** | Upload, staircase, retrieval with the trace. **No chat yet — retrieval results are the output** |
| **Phase 2** | Chat over the retrieved context; before/after-enrichment comparison |
| **Phase 4** | Bulk dataset upload, sample-first enrichment, cost estimate |
| **Phase 6** | Configuration A/B — models, chunkers, prompts |

**Phase 1 deliberately ships retrieval-without-chat.** The trace is the useful part and it is what
proves the spine; adding a conversational layer over a retrieval path nobody has inspected just
hides the thing worth looking at. It also keeps the honest ordering: if retrieval is wrong, chat
cannot be right, and a chat UI would let you ship without noticing.

---

## Requirements

- **FR-SBX-1** A sandbox MUST be an ordinary project with a TTL, running the identical write,
  enrichment and retrieval paths. There MUST NOT be a sandbox-specific code path.
- **FR-SBX-2** A sandbox MUST inherit the project's ACL defaults, privacy policy and model-routing
  allow-list. It MUST NOT relax any of them.
- **FR-SBX-3** The upload surface MUST state that ingestion is real, under real retention, and MUST
  name the expiry date.
- **FR-SBX-4** Sandbox expiry MUST run the standard deletion cascade.
- **FR-SBX-5** Bulk enrichment MUST default to a sample, and full enrichment MUST require an
  explicit decision with a cost estimate.
- **FR-SBX-6** The readiness staircase MUST be visible per dataset, with counts per state.
- **FR-SBX-7** A chat or retrieval response MUST state how many records were enriched of the total
  it searched.
- **FR-SBX-8** Retrieval results MUST show ranked chunks with scores, their source records, and the
  exact context passed to the model.
- **FR-SBX-9** Excluded records MUST be distinguishable by reason — ACL, score threshold, or not
  yet enriched.
- **FR-SBX-10** Every result MUST record `model_id` and `generator_version`, so a run is
  reproducible and two runs are comparable.
- **FR-SBX-11** The sandbox MUST support running one dataset under two configurations and
  comparing the retrieval traces.
