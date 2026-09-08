# mem-dog vs the "company brain" category

**Last updated:** September 2026 · **Confidence:** lower than the Onyx and Glean
comparisons, and the reason is in [What this document is not sure about](#what-this-document-is-not-sure-about).

"Company brain" is a 2026-native term. Several companies adopted it in the first
half of the year — Falconer, nBrain, Ability.ai and others — and the category is
young enough that its boundary is still being drawn by the people selling into
it. That makes it worth comparing against carefully rather than confidently.

The working definition in circulation: **a shared, learning memory layer that
captures how an organization actually operates — decisions, conventions, context
— and serves it to humans and AI agents on demand.**

Read that definition next to what mem-dog does and the difference is not a
feature list. It is what each one thinks memory is *of*.

---

## The distinction that matters

**A company brain curates how you work. mem-dog reads what arrived.**

A company brain's subject is organizational knowledge: the decision, the
convention, the runbook, the reason. Its central problem is *freshness* — that
knowledge decays, and the product's job is to notice and repair it. Falconer
states this directly: documentation that "keeps itself organized and up to date"
by syncing with GitHub, Slack and Linear.

mem-dog's subject is the record: the email, the ticket, the call, the calendar
entry, the contract. It owns no documents and has no editor. Its central problem
is *retrieval you can check* — which passage, from which record, and what was
left out.

These are not competing answers to one question. They are answers to two
questions that sound alike:

| | asks | answers from | fails when |
|---|---|---|---|
| **Company brain** | "How do we do this?" | curated knowledge, kept fresh | the curation lapses |
| **mem-dog** | "What actually happened?" | records as they arrived | nothing was recorded |

The same team can want both, which is the honest reason this is not a
head-to-head.

## Where they genuinely overlap

Three places, and they are worth naming rather than dismissing.

**Context for agents.** Both sell "reliable context for AI agents" — Falconer
explicitly. mem-dog's MCP surface and retrieval API are the same pitch aimed at
the same buyer. This overlap is real and growing.

**Change over time.** Falconer's core mechanic is noticing that documentation has
drifted from the systems it describes. mem-dog's checkpoint timelines do the
structurally similar thing for a record fed repeatedly — *what changed since last
time*, with the comparison stored as an artifact. Different subject, same
observation: a stale answer that looks current is worse than no answer.

**Connectors.** GitHub, Slack, Linear on one side; a 37-entry connector catalog
on the other. The overlap is the sources, not the intent — a company brain reads
Slack to keep a document true, mem-dog reads it to answer about what was said.

## Where mem-dog is ahead

Stated against what these products publish, not against what they might do.

**Citations with offsets.** Every mem-dog answer names the passage it rests on
and can open the source at the sentence. Falconer's site does not mention source
attribution at all. For a product whose output is a *document*, that is a
reasonable omission; for a product whose output is an *answer*, it is the
difference between something you can check and something you must believe.

**What was left out.** The retrieval trace — arms, scores, and records
considered and dropped with the reason — is not something any product in this
category appears to offer. An answer that searched almost nothing and an answer
that searched everything look identical without it.

**A permissions model.** mem-dog has per-document ACLs, group principals
resolved at query time, and audit on every read. Falconer's site does not mention
access control. That is a common gap in young products and a serious one for the
buyer this category targets.

**Self-hosting and data ownership.** nBrain markets "private, company-owned,
model-agnostic, you own everything", so this is *not* a mem-dog differentiator
against that part of the category — but it is against the SaaS half of it. The
[landscape document](README.md) already retires self-hosting as a general moat,
and that judgement holds here.

## Where the category is ahead

**Authoring.** A company brain produces documents people read and edit. mem-dog
produces answers and derived artifacts; it has no editor and does not intend to
have one. For a team whose actual problem is "our runbooks are wrong", mem-dog
does not solve it.

**A narrower, more legible promise.** "Your engineering docs stay true" is easier
to buy than "a memory layer that shows its work". Category clarity is a real
advantage and mem-dog does not have it.

**Curation as a feature, not a gap.** These products are opinionated about what
belongs in the brain. mem-dog ingests what it is pointed at and sorts it later,
which is more general and less immediately useful.

## What this document is not sure about

The Onyx and Glean comparisons rest on published documentation, and both of those
products are mature enough to have some. This one does not have that footing:

- **The category is months old** and its members are mostly early-stage. Feature
  claims come from marketing pages, which are the least reliable source there is.
- **Falconer is the only member examined in any detail**, from its own site in
  September 2026. nBrain and Ability.ai are named from search results and not
  verified.
- **No product in this category was used.** Nothing here is a hands-on judgement.
- **Pricing is unknown** for all of them.

Treat the structural argument as the durable part and every specific claim as
provisional. If this category matters commercially, it deserves the same
treatment the Onyx comparison got — which was research, not a search.

## What to take from it

**The overlap that will grow is agent context**, not documentation. Both sides
are converging on "be the thing an agent reads before it acts", and that is where
a genuine collision happens.

**The thing worth borrowing is the freshness mechanic.** These products treat
decay as the central problem and build the whole product around noticing it.
mem-dog has the machinery — checkpoints, staleness on derived artifacts, the
reconciler — and does not present it as a promise. That is a positioning gap more
than an engineering one.

## Sources

- [Falconer](https://falconer.com/) — product page, read September 2026
- [nBrain](https://clients.nbrain.ai/) — product page
- [Vectorize: How to build a company brain](https://vectorize.io/articles/how-to-build-company-brain) — category definition
- [Vectorize: The brain stack](https://vectorize.io/articles/brain-stack-second-company-single-brain) — second / company / single brain framing
