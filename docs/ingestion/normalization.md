# Normalization

## Three layers, currently conflated

The proxy already supports `?normalize=contact|calendar_event` — the right idea, covering two
types, on the *outbound* path rather than on ingestion.

| Layer | Transforms | Status |
|-------|-----------|--------|
| **L1 Transport** | provider payload → `UniversalEnvelope` | built — gateway |
| **L2 Domain** | provider record → canonical object | **the gap** — 2 types, proxy-only |
| **L3 Output** | agent result → schema | built — `schema_override` |

## Where it belongs

Normalization is its own stage, *before* enrichment — not inside the agents.

```
classify → NORMALIZE → route → enrich (LLM) → embed → entities → graph
              ▲
              │ provider profile mapping + target schema
              │ (mem-dog standard OR user standard)
```

Three reasons it cannot live inside the agents:

1. **~60% of the catalog is JSON records** that should normalize with no inference call at all.
2. **It makes entity extraction structural rather than inferred.** If a Salesforce record
   normalizes to a canonical `Person`, you do not need an LLM to *guess* there is a person here.
3. **It must be regenerable independently.** Schemas change; re-normalizing should not require
   re-running expensive enrichment.

## Canonical types

Aligned with the existing eight graph entity types rather than a parallel taxonomy — a mismatch
creates permanent translation loss.

| Type | Sources that map to it |
|------|------------------------|
| `Person` | CRM contacts, email senders, chat users |
| `Organization` | Accounts, companies, workspaces |
| `Message` | Chat, email, comment, review |
| `Document` | Files, docs, pages, articles |
| `Event` | Calendar, meeting, recording |
| `Task` | Issue, ticket, story, todo |
| `Transaction` | Invoice, payment, charge, order |
| `Activity` | Log, alert, incident, event |

## Customization: mem-dog standard or user standard

Follow the pattern proven by `agent_configs` — schema in the record store, read per invocation,
no redeploy.

```
NormalizationSchema          scope: global | org | project
  target_type                Person | Invoice | <user-defined>
  json_schema                the shape
  version                    versioned, never mutated in place

FieldMapping                 per (provider, target_type)
  source_path  →  target_field
  transform                  cast, split, concat, date-parse, lookup
  required / default
  on_missing                 skip | null | fail
```

**Precedence:** project → org → mem-dog standard. Most specific wins, consistent with the rest of
the tenancy model.

Authoring path, increasing in power: use a standard type → extend it → define a custom type →
override the provider mapping. Most users stop at step one or two.

## Two invariants

**Raw is truth; normalized is a derived view.** Never discard the original — normalization is
lossy, schemas evolve, mappings have bugs. Store the projection alongside, tagged with the schema
version that produced it.

**Normalization failure must never lose data.** A record that does not fit lands raw with
`normalization_status = failed` and a reason, and stays retryable.

## LLM-assisted mapping

Worth building a *suggester* — point it at a sample payload, get a proposed mapping. But the
mapping it produces must be stored declaratively and be reviewable. Runtime LLM mapping would be
nondeterministic and expensive, and identical inputs would normalize differently across runs.

## What it unlocks

- **Cheaper, more accurate entity extraction** — structural rather than inferred
- **Typed search facets** — `amount > 10000`, `assignee = X` become filterable
- **Cross-source identity resolution** — the same canonical `Person` from CRM, email and chat gives
  the graph far stronger merge signals than prose does
