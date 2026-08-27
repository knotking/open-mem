# Retrieval Quality — Feedback and Conflict

Two things the design detects but never acts on: whether an answer was any good, and what to do
when sources disagree.

## Feedback

Nothing currently captures whether a result was useful. That is the signal that would evaluate a
[prompt override](../ingestion/workers.md), justify a reranker, or tell you an index type is not
earning its cost — and it is cheap to collect at retrieval time if designed in early, and
impossible to collect retroactively.

| Signal | Kind | Cost to collect |
|--------|------|-----------------|
| Explicit rating on an answer | strong, sparse | a thumb |
| Citation opened | implicit, dense | one event |
| Result opened, then a refined query | implicit — a **negative** signal | free |
| Answer copied or acted on | strong, rare | one event |
| Query abandoned with no interaction | weak negative | free |

**The refinement signal is the most useful and the most overlooked.** A user who searches, opens
nothing, rephrases and searches again has told you the first result set was wrong — with no rating
and no complaint.

### Feedback cannot be pooled across tenants

The obvious use is to learn a better ranking. The obvious implementation is to learn it from
everyone's feedback at once.

**That leaks.** A model tuned on one tenant's click behaviour encodes what their corpus contains and
what they look for. Ranking learned across tenants is a side channel, and a subtle one — nobody
sees another tenant's document, but the ranking function carries information about it.

So: feedback is **tenant-scoped by default**. Cross-tenant learning is opt-in, aggregated, and
should probably not exist in v1 at all.

### What it is safe to use immediately

- **Evaluation, not training.** Feedback against a golden set tells you whether a prompt or
  reranker change helped — without any model consuming it
- **Per-tenant reranking signals** — a document repeatedly chosen for similar queries in *this*
  workspace
- **Index-value measurement** — if the question index never contributes to a chosen citation, it is
  not paying for itself

## Conflict

The [claim index](indexes.md) detects contradictions. Nothing says what to do with one.

Two sources say the approval threshold is $5,000 and $10,000. Someone asks. What comes back?

### Resolve what is resolvable; surface the rest

| Conflict | Resolution |
|----------|-----------|
| **Temporal** — the same fact changed over time | Already solved: `valid_at` / `invalid_at`. Not a conflict, a history |
| **Supersession** — a document revised | The [mutation path](../ingestion/workers.md) invalidates facts from the superseded version |
| **Source authority** — a system of record disagrees with a chat message | Rank by **declared source authority**, per producer or connection |
| **Genuine disagreement** — two authoritative sources differ | **Surface both.** Do not pick |

### Source authority is declared, not inferred

A producer carries an authority level. The HR system is authoritative for employment facts; a Slack
message mentioning someone's title is not. That is a configuration a human makes, not something to
infer from confidence scores.

Without it, "most recent wins" becomes the default — which means a passing remark in chat overrides
the system of record because it arrived later.

### Never silently pick one

When authority does not separate them, the answer says so:

> The approval threshold is **$10,000** according to the Finance Policy (updated March),
> though the Procurement Handbook [2] states $5,000.

**A confident wrong answer is worse than an uncertain right one.** The system knows there is a
conflict — the claim index found it — and hiding that to produce a cleaner sentence is the failure
mode this whole design has been avoiding everywhere else.

## Requirements

- **FR-QUAL-1** Retrieval MUST capture explicit and implicit feedback, including query refinement
  as a negative signal.
- **FR-QUAL-2** Feedback MUST be tenant-scoped. Cross-tenant learning MUST be opt-in and MUST NOT
  be enabled by default.
- **FR-QUAL-3** Feedback MUST be usable for evaluation without being consumed by a model.
- **FR-QUAL-4** Producers MUST carry a declared source-authority level.
- **FR-QUAL-5** Detected conflicts MUST be resolved by authority where it separates them, and
  **surfaced with both positions** where it does not.
- **FR-QUAL-6** An answer MUST NOT silently present one side of a detected conflict.
