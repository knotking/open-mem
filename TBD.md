# TBD — Decisions Open for Parag

Everything here is **designed but not decided**. Each has a recommendation and a stated cost; none
is blocked on more analysis. They are listed in the order that matters — how expensive the decision
becomes if it is made late.

> **Why the ordering matters more than the list.** Several of these are cheap now and close to
> unfixable later, and the difference is not how hard they are to build. It is whether data has
> already been written under the other assumption.

---

## Tier 1 · Expensive to reverse once data exists

### 1 · Embedding dimension

**The question.** What output dimension does the embedding column use?

**Why it is here.** It sets the `pgvector` column width. Increasing it later is a **full re-embed
of the corpus** — not a migration.

**What changed the answer.** Both leading models are **Matryoshka-trained**, so the first N
dimensions are a valid embedding on their own. **Reducing is a truncation of stored vectors; only
increasing costs a re-embed.**

> **Recommend: store at the larger dimension, index at whatever performs.** Storage and
> filtered-ANN latency are recoverable decisions. Re-embedding a corpus is not.

*Corrects earlier guidance in these documents that said smaller-by-default.*

### 2 · Media in scope for v1?

**The question.** Does v1 transcribe audio and video, and interpret images?

**The cost either way.** Media is the **largest cost exposure of any format group** and needs
transcription infrastructure. But recordings are where meeting intelligence and clinical imaging
live, and the [demo's clinical domain](docs/operations/onboarding.md) is thinner without it.

**Recommend:** out of v1, **stored but not interpreted** — DICOM and recordings land as `Stored`
refs with mime types, so nothing is lost and interpretation is added later without re-ingesting.
MedGemma makes clinical imaging locally feasible when it arrives.

### 3 · Add write phase 5 — transform and redact?

**The question.** Can a project strip an SSN *before* anything is persisted?

**Why it is here.** Today the only tools are downstream: classify after storage, then run an
erasure cascade. **Not storing is categorically better than storing and deleting** — the data
existed, it was backed up, and the cascade has to reach every derived artifact.

**Recommend: yes.** Cost is a declarative rule type and the discipline that it never calls a model
inline. Note that **W7 cannot un-redact**, so narrowing a rule later is irreversible and needs a
preview.

### 4 · Default ACL for a team upload

**The question.** A user drags a file into a *team* project. Private, or team-visible?

**The tension.** Private-by-default is consistent with everything else in the design. But users
dropping a file into a shared space **often expect team visibility**, and a default that surprises
people in the safe direction still produces a product that feels broken.

**No recommendation.** This is a product judgement about what your users expect, not an
architectural one — and it is **very hard to change once habits form**.

---

## Tier 2 · Scope decisions with a stated trade

### 5 · Scope W10 standing queries?

**The question.** Does the platform push, or only answer when asked?

**Why it matters.** Retrieval is pull-only, and **four published use cases need push** — media
monitoring, IoT thresholds, legal deadlines, compliance retention. **Media monitoring is *only* a
push product**; shipping it without delivery ships nothing.

**Recommend:** scope W10 with media monitoring, and **state the deferral explicitly** for the other
three rather than letting it be discovered. Two of three pieces already exist — a saved selector
and a webhook — the missing part is the evaluation loop.

### 6 · `compress` → `derive`?

**The question.** Is summarisation the only artifact derivable from a memory?

**Why it matters.** Study guides, flashcards, obligation extracts and customer briefings are **one
operation with different output schemas**. Hardcoding "summary" makes each a bespoke endpoint.

**Recommend: yes.** It reuses the existing generator registry, and the endpoint has no clients yet —
which is the cheapest this rename will ever be.

### 7 · Allow `reject` as a validation policy?

**The question.** Can a regulated project refuse non-conforming data at the door rather than
storing it flagged?

**Recommend: yes, but blocked for webhook producers.** A webhook receiving a `4xx` either retries
forever or drops silently — enabling `reject` there **converts a compliance preference into silent
data loss**. Say so at configuration time rather than letting it be discovered.

### 8 · Backfill depth on first connect

**The question.** 30 days, one year, or everything?

**Why it matters.** It materially changes W3 sizing, the first-run cost, and how long a new user
waits before the product is useful.

**No recommendation** — it depends on whether you are optimising for time-to-value or for cost
predictability, which is a positioning decision.

### 9 · Materialisation policy for fetched bytes

**The question.** Always store fetched bytes, store under a size threshold, or derived-only?

**Recommend: always store.** Predictable, and retries become free. Derived-only is cheapest and has
the strongest privacy story, but it is unrecoverable and re-processing then depends on the source
still existing. Revisit if volume demands it.

---

## Tier 3 · Boundary decisions

### 10 · Cross-project cases

**The question.** Can a case span projects?

**Why it is here.** Spanning **breaks the project isolation boundary everything else relies on** —
every query is scoped to a project, and a case that crosses one is a hole in that scoping.

**Recommend: no**, until there is a concrete need that cannot be met by correlating identifiers at
the query layer instead.

### 11 · Is air-gap-being-untrue-in-MVP an accepted trade?

**The question.** MVP inference is Gemini plus Ollama Cloud. **Air-gapped operation and $0 local
inference are therefore not true in MVP** — and both are load-bearing in the competitive
positioning.

**The mitigation already taken.** Ollama Cloud speaks the same protocol as local Ollama, so
restoring air-gap is a **base-URL change against an adapter already in production**, not a new
integration.

**No recommendation** — this is a positioning call. It should be **a stated trade rather than
something discovered** by a prospect who read the self-hosted claim.

### 12 · The licence question

**The question.** Proprietary, or permissive?

**Why it is here.** mem-dog competes **proprietary against MIT and Apache incumbents in every
adjacent category** — Onyx is MIT with SOC 2 and permission-aware retrieval; Mem0 is Apache 2.0.

**No recommendation.** Competing proprietary in a developer-tools category is a legitimate choice
with consequences. It should be **made deliberately, not defaulted into**.

---

## Not on this list, and why

Three things that look like open decisions and are not:

| Looks open | Actually settled |
|------------|------------------|
| Which models to use | **Gemini Flash + Gemini embeddings, plus Ollama Cloud.** All pinned, embeddings single-engine |
| Whether MVP has customization | **No.** Three enforcement points ship anyway — phase order, read-only security fields, `handler_digest` in the fingerprint |
| Whether bulk and account deletion are Phase 1 | **Yes.** The run entity was already latent in the sandbox |

---

## How to use this file

Each decision is safe to make in isolation — none blocks another. When one is decided:

1. Record it in [`docs/roadmap.md`](docs/roadmap.md) under **Decisions → Closed**, with the reason
2. Remove it here
3. If it changes a shipped default, check [`docs/settings.md`](docs/settings.md) — **defaults are
   policy**, and the register is where that policy is stated

**Full context for every entry** is in the linked documents and in the
[blueprint artifact](https://claude.ai/code/artifact/c8a9e266-fef7-4b68-b521-dceef8d0574e).
